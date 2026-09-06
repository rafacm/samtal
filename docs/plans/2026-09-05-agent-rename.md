# One name, rewritten everywhere it is still read

Plan for [#356](https://github.com/rafacm/vinga/issues/356).
Implementation notes land in the companion
`2026-09-05-agent-rename-implementation.md`, one section per milestone,
appended in the change that ticks the milestone here.

## Goal

Renaming an agent today is delete plus create, and nothing can recognize
the pair as a rename, so every reference keyed by the name is left
pointing at a name nothing answers to. The store refuses the delete half
while a device binding or the default agent names it
(`config/store.py:429-432`), so the operator's route is: unbind the
board, clear the default, delete, create under the new name, bind again,
set the default again, and lose the memory. Six writes and a silent
loss, for one act.

This plan builds the act: `vinga agent rename <old> <new>`, one
transaction, every live reference rewritten or nothing written at all.
After it, a rename keeps what the agent remembered, what boards are
bound to it, whether it is the default, and the conversations it can
still be asked to resume.

What it deliberately does not do is rewrite the record of what happened.
A turn spoken last month was spoken by an agent whose name at the time
was the old one, and the row that says so is evidence rather than a
reference: no row that exists when the rename runs is edited by it. The
line between a record and a reference is drawn below from the code
rather than from intuition, and drawing it moved one column across from
where the issue put it. What a session still in flight writes *after*
the rename is a third case, and it is a new write rather than an edit:
[the order that covers those writers](#the-sessions-in-flight-and-the-order-that-covers-them)
says what each row it makes then carries.

## The issue's decisions, restated

- **A rename is one transactional rewrite, not a compensating sequence.**
  Settled, and not re-argued here.
- **The verb follows the CLI guide**: noun `agent`, verb `rename`, the
  address first and the new name as the payload behind it. Settled; the
  spelling is worked through against the guide's practices below.
- **Sequenced after #83**, which shipped the name keys and the
  documented orphaning. Both are merged (PRs #359 to #363), so the
  ground this stands on is in place.
- **Confirmation is this plan's to decide.** Resolved below.
- **The record schema stays as written.** This is the one line the plan
  reopens, and it reopens it on evidence rather than on preference:
  `record.conversations.agent` is not a dated row, it is the filter two
  live reads run at request time. See the census, and the resolution
  under [What is a live reference, measured](#what-is-a-live-reference-measured).
  The rest of the record (turns, sessions, tool calls, events, capture
  manifests) stays exactly as the issue says.

## Where the references are, and the census

Every count below is from a command, and every command is written out so
a reviewer can re-run it. Run from `vinga-server/` unless the command
says otherwise.

**The tables, read off the metadata rather than off a grep**, because a
column that holds an agent name is a fact of the declaration and a grep
for the word "agent" answers with prose:

```bash
uv run python -c "
from vinga_server.db import schema as domain
from vinga_server.conversations import schema as record
from vinga_server.memory import schema as memory
for name, module in (('domain', domain), ('record', record), ('memory', memory)):
    for table in module.metadata.sorted_tables:
        print(name, table.name, [c.name for c in table.columns])
"
```

Sixteen tables across the three schemas. The ones holding an agent name,
and nothing else does:

| Where | Shape | Read as |
| --- | --- | --- |
| `domain.agents.name` | text primary key | the identity itself |
| `domain.devices.agents` | JSON array of names | live, per check-in |
| `domain.domain_settings` where `key='default_agent'` | JSON scalar | live, per check-in |
| `memory.facts.owner` where `scope='agent'` | text | live, per turn |
| `record.conversations.agent` | text | **live**, per resume and per listing |
| `record.sessions.agent` | text | record |
| `record.sessions.agents` | JSON array | record |
| `record.turns.agent` | text | record |
| `record.turns.legs[].agent` | inside JSON | record |
| `record.events.fields` | inside JSON | record |

**Nothing cascades, anywhere, by construction.**

```bash
grep -rn "ForeignKey" src/ | wc -l   # 0
```

`db/schema.py:41-51` states the decision: "Referential integrity lives
in the repository rather than in database foreign keys." So there is no
`ON UPDATE CASCADE` to lean on and no constraint to be refused by: every
reference above is an uncorrelated copy of a string, and the rewrite has
to name each one.

**What the repository itself calls a reference to an agent** is one
function, and it is the inventory this plan rewrites the domain half
against rather than a list written beside it:

```bash
grep -n "snapshot.agents" src/vinga_server/config/models.py
```

`check_references` (`models.py:3216-3271`) walks exactly two: the
default agent, and every entry of every device binding. It already runs
inside every write, which is what refuses the delete half today, and it
is what the rename runs to prove it left nothing dangling.

**The memory half is one column under one scope.**

```bash
grep -rn "facts\.c\.owner" src/vinga_server/memory/store.py | wc -l   # 13
grep -n "class MemoryScope" -A 20 src/vinga_server/memory/scopes.py
```

`owner` means an agent name when `scope = 'agent'`, a canonical MAC when
`scope = 'device'`, and the conversation scope never reaches `facts` at
all (`scopes.py:24-47`, `FACT_SCOPES = (AGENT, DEVICE)`). `memory.state`
is keyed by conversation and holds no agent name. Held rows, the ones a
soft forgetting parked with `forgotten_at`/`forgotten_in`, carry `owner`
like any other row, so they move with the rename and a restore after it
still finds them.

### What is a live reference, measured

The issue's line is the right one and one column sits on the wrong side
of it. The line: **a reference something reads to decide what happens
next is live; a column recording what was true at a moment is record.**
Applied with a grep rather than with a reading:

```bash
grep -rn "conversations\.c\.agent" src/   # 5 sites, two of them filters
grep -rn "turns\.c\.agent" src/           # 0 sites
grep -rn "sessions\.c\.agent" src/        # 1 site, a projection in a listing
```

- `conversations.c.agent == agent` at `conversations/threads.py:540`
  (the thread listing's filter) and `:661` (`candidates`, "the threads of
  one agent a spoken description might have meant"). `resumption.py`
  keys its offers and its awaits on the agent name (`:141-163`,
  `:200-257`) and re-checks ownership at `:362-366`, and
  `threads.py:103,293` refuses a thread whose agent does not match with
  `ANOTHER_AGENT`. So a rename that leaves this column alone detaches
  every past thread from the agent that owns it: resume by description
  goes empty, `vinga conversation list --agent` goes empty, and the
  thread guard refuses the agent its own history. Silently, because
  `AgentQuery` is "matched exactly and not refused for shape"
  (`conversations/api.py:354-364`), so a name nothing answers to is an
  empty page rather than a mistake.
- `turns.agent` appears in **no** WHERE clause in the tree. It is
  written and displayed. `sessions.agent` is projected into a listing and
  filtered on nothing. `turns.legs[].agent` and the event fields are
  inside JSON that nothing queries by agent.

So `conversations.agent` moves and no other record row is edited, and
the mismatch that leaves is the honest one: a thread belongs to the
agent under its current name, and each turn inside it records the name
the agent had when it spoke. That is stated where both are rendered
rather than left for somebody to find. The one thing this table does not
settle is what a row *written after* the rename by a session that has
not heard about it should carry, which is a question about a write
rather than about an edit and is answered in full below.

### The rest of the surface, and why it needs nothing

**In-process structures keyed by agent name** were swept
(`grep -rn "agents\[" src/`, plus a read of each): `ProviderWorld.agents`
(`providers/world.py:90-104`), `Fillers` (`filler.py:96-149`),
`McpSlice.grants` (`tools/mcp/slice.py:209-292`),
`PipelineRuntime._agents`/`_conversations` (`runtime/pipeline.py:687-698`),
`resumption._offered`/`_awaiting`, and the `switch_agent` tool's enum
(`tools/builtin.py:80-107`). Every one of them is rebuilt from the
configuration at the apply that installs it, and none is written back to
a store. A rename therefore reaches them the way every other domain
write does, at the boundary the acknowledgement names, and needs no
invalidation of its own.

**A conversation in flight keeps the world it opened with**, which is
already designed for and already tested. `_activate_agent` reads through
`_world_of` (`runtime/pipeline.py:851-870`), whose whole job is "the
session's own [world] when the current world has never heard of this
agent, which is exactly the state an apply that deleted it leaves
behind" (#191). A rename looks like exactly that to a live session, so
it lands on a path that exists: the session goes on talking as the old
name, and its `switch_agent` list keeps working. What that costs, and
what has to be done about the writers behind such a session, is
[the order that covers them](#the-sessions-in-flight-and-the-order-that-covers-them)
and [the window that stays](#the-window-that-stays).

**Nothing else addresses an agent by name.** The simulator addresses a
board and lets the binding resolve (`grep -rn "agent" src/vinga_server/simulator/*.py`
finds two prose lines and no lookup). `prompt_fragments`, `providers`,
`mcp_servers` and `agent_defaults` hold no agent name: they are
referenced **by** an agent, never the other way, and `agents.body` holds
outbound references only, the name living in the primary key alone
(`db/schema.py:1-52`). Pending device claims carry the agent in the
request and store none.

### What an agent name is allowed to be

The rename's validation cannot be stricter than the `set` that created
the name, and cannot be looser than the path that addresses it.

```bash
grep -n "NonBlankStr = " -A 2 src/vinga_server/config/models.py   # 165-167
grep -n "def _identifier" -A 8 src/vinga_server/config/store.py   # 2110-2115
grep -n "def _check_addressable" -A 12 src/vinga_server/config/store.py  # 2467
```

The whole rule: stripped, non-empty, no `/`, no C0/C1 control
character. There is no regex, no length cap, no reserved word and no
case folding, and `events/values.py:262-276` says so in as many words
("an agent called `secondary"agent` is lawful configuration today").
Names are therefore case-sensitive, may hold dots, spaces, quotes and
anything outside ASCII, and `sam` and ` sam ` are one agent because
every path strips first (`store.py:1556-1580`, `models.py:3596`).

Two consequences the rename inherits rather than decides:

- **A stored name can violate the rule**, because the load path never
  re-checks (`_check_addressable`'s docstring says so). Such a row is
  unaddressable over the API today and stays unaddressable after this:
  `POST /agents/{name}/rename` cannot reach a name holding a slash, for
  the same reason `GET /agents/{name}` cannot. This is measured already
  by `config/views.py:127-144` and is not this issue's to fix.
- **A stored name can carry a URL credential**, which is why #381 put
  every display through `without_url_credential` and #382 put every
  refusal that names a stored identity through it too
  ([the boot refusal's location policy](../features/2026-09-05-boot-refusal-location-policy.md)).
  The rename's sentences follow that policy exactly, below.

## Open questions, resolved

### Confirmation: none, and the guide's own line is why

The CLI guide's line is that "a verb destroys when its effect cannot be
undone by running another command with information the operator still
has" (`cli-guide.md:947-951`), which is why a delete confirms and a
`set` does not. A rename is undone by `vinga agent rename <new> <old>`,
with information the operator has in the shell history of the command
they just typed, and it moves the memory and the threads back with it.
So
`destroys=False`, no confirmation, no `--force` row, and the verb does
not join the destructive rows in `cli.py`
(`grep -n "destroys=True" src/vinga_server/config/cli.py` finds nine,
one of them generated per entity kind).

That is only true while the rename cannot merge two things into one,
which is what makes the destination rule below load-bearing rather than
tidy. Renaming onto a name that already holds memory rows, or threads,
merges two pasts, and afterwards nothing can tell them apart: a rename
back would carry the strangers with it. **The refusals are what keep the
verb reversible, and the reversibility is what keeps it out of the
confirmation table.** If a later change ever licenses a merge, the verb
becomes destructive on that day and the row says so.

Rejected alternative: confirming anyway, because a rename "feels"
alarming. The guide's counterexample to that is its own rule that the
line is drawn by reversibility rather than by alarm, and a prompt in
front of a reversible act teaches an operator to type `--force`
everywhere, which is where the prompt in front of an irreversible one
stops being read.

### Which refusals exist, as a closed set

Seven, and the set is closed because each is a state the transaction can
be in before it writes anything. Each is one fixed sentence in
`config/store.py` beside the sentences the other writes use.

**The rule they come from is one sentence: the destination name is free
everywhere this rename would write.** Free in the domain half, free in
memory, and free in the record's one live column. It is a single rule
rather than three checks because it is what reversibility means: a
rename may never merge two pasts into one, since no second rename can
tell them apart afterwards.

| State | Answer | Status |
| --- | --- | --- |
| no agent under the old name | `NO_SUCH_AGENT`, the sentence every agent read already uses | 404 |
| an agent already exists under the new name | fixed sentence, naming neither | 409 |
| memory rows already exist under the new name | fixed sentence, naming neither | 409 |
| conversation threads are recorded under the new name | fixed sentence, naming neither | 409 |
| the new name is not addressable (slash, control character, empty once stripped) | `_identifier`/`_check_addressable`, unchanged and reused | 422 |
| the new name is the old name once stripped | fixed sentence | 422 |
| the database is contended, or a lock times out | `DatabaseBusyError`, unchanged | 409 |

Notes on four of them.

- **The memory collision is a real state and not a theoretical one.**
  Memory rows outlive the agent they belong to by design: the listings
  answer "every name that has a row, whether or not this deployment
  still has an agent of that name" (`memory/api.py:453-459`), which is
  the audit door #83 built. So an operator renaming onto a name that a
  deleted agent used still has rows under is asking for a merge. It is
  refused, and the remedy is a verb that already exists,
  `vinga memory delete agent <name> --all`, which the refusal names in
  the server's own spelling exactly as the five refusals catalogued by
  [the state-vocabulary plan](2026-09-05-server-state-vocabulary.md) do.
  That class is [#410](https://github.com/rafacm/vinga/issues/410)'s to
  fix wholesale, and this refusal joins it rather than inventing an
  eighth shape.
- **The thread collision is the same state in the record, and it is why
  the memory rule alone is not enough.** A thread outlives the agent
  that wrote it exactly as a fact does: `record.conversations` carries no
  foreign key, an unbound agent can be deleted while its threads stay,
  and retention prunes them by idleness rather than by ownership. If
  threads already sit under the destination name, moving the source's
  threads onto it merges two histories that nothing can separate again:
  a rename back would carry the strangers with it, and the thread guard
  would then hand one agent another's past. So it is refused under the
  record chain's lock inside the same transaction that would do the
  update, which is what makes the check and the write one decision
  rather than two. The remedy is the listing and the deletion the
  conversation noun already has, named in the server's own spelling like
  the one above and joining the same #410 class.
- **Renaming the default agent, and renaming a bound agent, are
  ordinary.** They are the cases the six-write workaround cannot do at
  all, and they are the reason the rewrite is one transaction:
  `check_references` runs at the end over the candidate state and finds
  nothing unresolved, because the binding and the setting moved with the
  row.
- **Case is a rename like any other.** `Sam` and `sam` are two keys, so
  `rename Sam sam` is a real rename, and there is no folding anywhere to
  make it a no-op. Renaming to a name that differs only in trailing
  whitespace is the same-name refusal, because everything strips first.

**The three conflicts need a class of their own, and it is why they can
answer 409.** `REFUSAL_STATUS` maps the listed `ConfigError` subclasses
and sends everything else to 422 (`config/api.py:307-342`), so a plain
`ConfigError` would make a destination collision look like a malformed
request. `config/loader.py` gains `AgentRenameConflictError` beside
`DeviceAlreadyBoundError`, which is the same kind of fact about the
world rather than about the request, and `REFUSAL_STATUS` gains the row
mapping it to 409. Both land in the milestone that raises it rather than
in the one that adds the route, so the type and its status never exist
apart, and a test asserts the mapping rather than trusting it. The three
states share one class and differ by sentence, because what a caller
does about them is the same: choose another name, or clear what is under
this one.

**And the sentences quote nothing the caller typed.** The converged
location policy says a stored entity's name is repository vocabulary a
refusal may speak, stripped through `without_url_credential`, while a
key the operator typed is not. In a rename the two provenances are one
argument apart: the **old** name is a stored identity, and the **new**
name arrived in this request, so it is caller text and is never echoed,
in any refusal, on either side of the API. The collision refusals
therefore name the rule and not the value, which is also what
`check_references` does with every name it could not resolve
(`models.py:3227-3238`) and what `defined()` licenses instead: the names
that DO exist may be listed, because this deployment wrote them.

### The route: a POST on the agent, and what it answers

`POST /agents/{name}/rename`, body `{"to": "<new name>"}`.

- **Why a POST.** The rename is not idempotent: run twice, the second
  run finds no agent under the old name and answers 404. `PUT` would
  promise a repeatable write and `PUT /agents/{name}/name` would read as
  an attribute with a `GET` beside it, which there is not. The merged
  precedent for a non-idempotent action on an addressed row is
  `POST /devices/pending/{code}` (claim), and `POST /apply` beside it.
- **Why the path shape.** `rename` is a trailing segment with no
  identity after it, which under the CLI guide's derivation rule
  (`cli-guide.md:404-434`) is an attribute of its parent and becomes a
  verb on the noun. `/agents/{name}/rename` therefore yields
  `agent rename <name>` with the payload behind it, and the identity
  depth stays at one.
- **Why a body rather than a second path segment.** A second segment
  would make the new name part of the address, which it is not: it is
  what the request carries. `_sole_value(body, "to", ...)` is the merged
  reader for exactly this shape (`config/api.py:2773` reads the device
  binding's `agents` key the same way), and the handler hands the value
  to the repository unread, per the rule that a body may carry a pasted
  credential and FastAPI's own validation echoes what it rejects
  (`config/api.py:2141-2147`).
- **The answer is `Acknowledgement`**, the shape every domain write
  answers with: `wrote`, `notice`, and the `applies` tuple #386 landed.
  The line is composed from what the transaction did rather than from
  what the request said, the way `bind_device`'s is
  (`config/api.py:2499-2504`), and the old name goes through
  `without_url_credential` on the way into it because it is a stored
  identity, which a row written before the addressability rule can make
  load-bearing. That strip is belt and braces rather than a reachable
  path, for the reason the tests record: a name carrying a credential
  carries a slash, and no path segment addresses one.
- **No counts in the body.** How many facts and how many threads moved
  is information about this invocation rather than about the artifact,
  the `Acknowledgement` shape is one shape for every domain write, and
  the audit door for what memory holds is the memory listing. Recorded
  as a decision rather than an omission; what would change it is an
  operator who cannot tell whether the memory moved, and the answer then
  is a stderr line rather than a widened wire shape.

### The boundary it announces: three arms, and one new sentence

A rename crosses two clocks at once, and which ones depends on what it
rewrote. The `applies` vocabulary is exactly the tool for that, and this
is its first user that computes the set rather than reading it off a
descriptor.

| What moved | `applies` | Sentence |
| --- | --- | --- |
| the agents row alone | `(RELOAD,)` | `APPLY_NOTICE`, unchanged |
| a binding or the default agent moved with it | `(RELOAD, CHECK_IN)` | a sixth `Notice`, new |
| the server was handed its configuration | `(STORE_BOOT,)` | `SNAPSHOT_NOTICE`, unchanged |

The middle arm needs its own sentence and cannot borrow
`BINDING_UNSERVED_NOTICE`. That one says "The binding applies at the
device's next OTA check", which is a sentence about one binding written
for `device bind`; a rename may have moved several bindings and the
default agent, and none of them is what the operator just wrote. The new
sentence says what is true of a rename: the stored references now carry
the new name, the running server is still serving the old one, and a
bound device reaches the renamed agent at the check-in after the
install. It names no command, per #386, and the client answers the token
set out of `cli.REMEDIES`.

**And it does not consult the loaded agents, deliberately**, which is
where it differs from `device bind`. `_binding_notice` asks whether the
running server already serves the named agent, and for a bind that
question is sound. For a rename it is not: the running server may serve
an agent under the new name and it will not be this one, because a
rename is precisely the act that moves a name onto a different body. An
"already serving" claim built on that lookup would be wrong exactly when
it mattered, so the middle arm is chosen by what the transaction
rewrote, which is a fact of this write.

### The sessions in flight, and the order that covers them

A live session holds the agent's name for as long as it lasts
(`runtime/pipeline.py:793-797`, and `_world_of` keeps it on the world it
opened with), and the rename moves the store underneath it. Left alone,
that is not a cosmetic mismatch, it is two defects:

- **A materialized thread drops the rest of the conversation.**
  `threads.landed()` refuses a landing whose agent does not match the
  stored `conversations.agent` with `MisattributedTurn(ANOTHER_AGENT)`
  (`threads.py:261-294`), and the durable writer answers any exception
  that is not a busy one by failing the marker's whole batch
  (`conversations/store.py:1038-1074`). So every turn spoken after the
  rename is lost, silently, and so are the ones batched with it.
- **A thread that has not materialized recreates the detachment.** The
  other branch of `landed()` INSERTs a conversation row from the
  landing, so a session that reaches its first turn after the rename
  writes a fresh row under the old name: a new live reference to a name
  no agent answers to, made by the very act that removes them.

Both come from one property the guard's own docstring states as an
invariant, that "a thread id is minted per agent and never shared". It
stays true; what stops being true is that a thread's agent has one name
for the thread's whole life. So the rename owes the writers an ordering
and a handoff, and the repository already has the shape of both,
because a thread erasure has exactly this problem and solved it.

**The protocol, which is the erasure's with a different fact
published.** `conversations/store.py:270-320` describes the hazard in
its own words: a store change is two steps, the transaction and the
publication to whoever holds rows in this process, and between the
commit and the publication a writer can slip in. `_erasure_order` covers
that instant, taken OUTSIDE every chain lock and never inside, held by
the changing side across its transaction and its publication, and by the
writer from before it opens a durable transaction until that transaction
has the chain lock and has read what was published.

The rename takes the same lock, for the same reason, in the same
position, and publishes a different fact:

1. Enter `erasure_order()` before the transaction is opened. The name is
   the erasure's today and the comment above it says the property is
   about writers rather than about deletions; it gains the rename as its
   second holder and the comment says so. Renaming the lock was
   considered and left to the milestone: the sentence it needs is
   "every store change a live writer must observe atomically", and
   changing a merged name is a rename this plan does not need.
2. Run the one transaction, taking the chain locks in ascending order
   under it, exactly as the erasure does.
3. After the commit and still inside the order, publish the rename to
   this process the way `erased()` publishes dead thread ids, through a
   sibling that reaches the same register of writers.
4. Leave the order.

**What a writer does with it, and where.** The conversation writer keeps
a map, **per session and not per process**, from a name that session may
still hand it to the name that name is now, and resolves it **once per
turn at the durable-write boundary**, before either row is built. The placement is the whole of this rule and it is
not free to move: `_write` inserts the turn row and then builds the
`Landing`, and `_turn_row` takes the agent straight off the record
(`conversations/store.py:1234-1264`, `:1342-1365`), so a translation
applied at the landing alone would file a turn under the old name onto a
thread under the new one, which is a row disagreeing with the row above
it. One resolution, one value, both uses: the turn's `agent`, each of
its `legs`, and the landing. Both defects close with it: the
materialized case matches the stored row and is stored rather than
dropped, and the un-materialized case INSERTs under the new name.
Chained renames stay flat rather than needing a walk, by composing on
insert: adding `old -> new` first rewrites every entry whose value is
`old`.

**Per session, because a process-wide map is not merely unbounded, it is
wrong.** A publication marks the sessions that are live at that instant
and no others, and each session's entries go when its state goes. The
counterexample that settles it: rename `sam` to `poet`, then create a
new agent called `sam`, which is now a free name, bind a board and let a
session open on it. A process-wide map still holding `sam -> poet` would
file that new agent's turns under `poet` and land its threads there.
Nothing about the size of the map protects against that, and nothing
about a session that opened after the publication needs translating:
its name came out of the store after the rename. So the entries belong
to the sessions that predate the rename, which is also what bounds them.

**And the retirement is one an existing lifecycle already runs.** The
writer keeps per-session state today, `_devices` among it, and pops it
after the close has been committed rather than when the close arrives
(`conversations/store.py:845-853, 950-958`). The map is popped in the
same places, so a queued batch still finds its translation on the way
out and a tombstoned session drops its entries with everything else. No
new lifecycle, no timer, and no entry that outlives the session that
needed it.

**What that decides about the record, stated as one line.** A row
**already written** is dated record and is never touched: the rename
rewrites no turn, no session and no event that exists. A row **written
after** the rename by a writer that still holds the old name is not a
dated row being edited, it is a new write, and a new write carries the
name the agent has now. The two halves of that line are what the plan's
earlier "the record stays as written" means precisely, and the third
case is the one that would otherwise be argued each time it came up:

| Written | Carries |
| --- | --- |
| `turns.agent`, after the rename | the new name: this turn was spoken now |
| `turns.legs[].agent`, after the rename | the new name, and for the same reason; a row may not disagree with itself |
| `conversations.agent` (materialize or move) | the new name, which is what keeps the thread reachable |
| `sessions.agent`, `sessions.agents`, even when the insert lands after the rename | the old name, verbatim: the column's subject is the moment the session opened, and that moment is before the rename |
| anything committed before the rename | unchanged, whatever it holds |
| emitted events and capture manifests | unchanged; they are what was said at the time and nothing rewrites them |

The `sessions` row is the interesting one, and it is deliberate rather
than an oversight: it says what the session opened with
(`conversations/schema.py:145-156`), so translating it would make it
answer a question it was not asked. A reader who sees a session opened
as one name whose turns were spoken under another is reading the rename,
which is what happened.

**And the process boundary, stated.** The register and the lock are
process-local, exactly as the erasure's are, and that is sound for the
same reason: the writer, the route and the publication are the one
server process, and the deployment is one replica by
[a recorded decision](../adr/2026-09-04-one-replica-is-the-supported-topology.md).
The break-glass local door is the exception and is safe by construction
rather than by the lock: it is for a deployment whose server will not
start, so there is no writer to order against. A rename typed through it
against a *running* deployment's database is outside the order, and the
CLI reference's door already says the local one is not for that.

### The window that stays

One, and it is stated rather than closed.

**Between the write and the apply**, the running server goes on serving
the old name: a session in flight keeps talking as it, per `_world_of`,
and what it remembers is written under the old name, so those rows are
new orphans the rename did not move. The window is the one every domain
write has, it is what the `applies` token tells the operator to close,
and the memory listing is where an orphan from it shows up. Moving the
memory at apply time instead was considered and rejected: it would split
one transaction across two clocks and make the rewrite conditional on an
apply that may never come.

**The memory store is deliberately not a second subscriber**, and the
asymmetry with the conversation writer is the point rather than an
oversight. The two consequences differ in kind: an untranslated landing
loses turns and manufactures a live reference, while an untranslated
`remember` writes a row the audit door already shows and the operator
door can already move. And the cost differs too: the conversation writer
has one boundary where a name enters (`landed`), while `MemoryStore`
takes the agent's name in eight session-facing methods, so translating
there would put the interpretation of a configured name in eight places
in a store whose whole interface is that name. Recorded here rather than
left to be rediscovered, with the publication named as the hook a
follow-up would attach to; the follow-up is filed in the milestone that
ships the protocol.

### The cross-schema transaction: three chains, ascending

The issue calls this a first. It is the first pairing of the domain
chain with the memory chain; the discipline, the helper and the stated
order are merged already, and the rename reuses them rather than
inventing anything.

`db.advisory_key`'s docstring (`db/__init__.py:239-257`) is where the
rule lives: "A transaction that writes two stores takes both chains'
locks, and the rule is that it takes them in ASCENDING key order". The
keys are domain 1, record 2, memory 3, all namespaced under `"ving"`.
The merged instance is a thread erasure, which holds 2 and then 3
(`conversations/api.py:803-856`, `memory/store.py:1207-1276`).

The rename's transaction is opened on the domain write engine, whose
begin listener has already taken key 1, and then takes 2 and 3 in that
order as it reaches each store. Ascending, and therefore incapable of
closing a cycle with the erasure that takes 2 then 3.

**The shape is `purge`'s, copied deliberately.** Each store publishes
one module-level function taking the caller's connection, taking its own
chain's lock as its first statement, and raising a classified failure
rather than swallowing one:

- `memory.store.rename_owner(connection, scope, old, new) -> int`,
  beside `erase_facts`, whose signature it follows.
- `conversations.store.rename_agent(connection, old, new) -> int`,
  beside `purge`'s own caller and beside the chain it has to lock.
  Beside the chain and not beside the SQL, which is the one placement
  question here and is settled by an import: `CONVERSATIONS_CHAIN` is
  declared in `conversations/store.py`, which already imports `threads`,
  so a locking function in `threads.py` would have to import the chain
  back and close a cycle. Putting it in the store also makes the record
  half the mirror of the memory half, whose `purge` and `erase_facts`
  live in `memory/store.py` for the same reason: a chain is a fact of
  the store that owns it (`db/__init__.py:24-27`). The statement it
  issues addresses `threads`' own table through the shared metadata,
  which is not an import of anything new.

`purge`'s docstring already states why the function takes the lock
rather than the caller: it is what makes the ascending order a property
of the function rather than of a call site somebody has to remember. The
two new functions say the same thing in their own words.

**Each of them checks its own destination, under its own lock, in the
transaction that would write.** Neither store can express the rule as a
constraint: nothing in either schema stops two owners becoming one, and
a count cannot report the difference between a destination that already
has rows and a source that has none, since both are ordinary. So each
function is check then update rather than update alone: it takes the
lock, asks whether anything is filed under the new name, raises the
typed conflict if so, and updates otherwise. The check cannot go stale
between the two statements, because the lock it is holding is the one
every writer of that chain takes at BEGIN, which is the property the
competing-write pin below asserts rather than assumes. The alternative,
one SQL expression answering both facts, was considered and left: two
statements under a held lock are what the rest of these stores are
written as, and a single expression would buy nothing a lock already
gives.

**And the conflict travels untranslated.** Both functions classify
failures the way `purge` does, into the busy refusal and the storage
refusal, and both let the typed conflict past that arm rather than
folding it into a 500: it is a state the caller can correct, not a
failure of the database. `_written`'s `except _Refused` arm in the
memory store and `_transaction`'s `except ConfigError: raise` in the
domain store are the two merged instances of exactly that shape, and the
new exception is a `ConfigError` subclass so both already carry it.

**One connection, three schemas, one database.** All three chains live
in one Postgres database, separated by schema and by their own Alembic
version table (`db/__init__.py:18-27`), and every table is
schema-qualified on its metadata, so one connection addresses all three
without a `search_path`. Failure atomicity is therefore free: any
refusal or any driver failure rolls the whole transaction back, and
there is no half-renamed state to compensate for. The pin below asserts
it by making the last statement fail.

**When a schema is absent or empty.** An empty memory or record schema
is nothing: the two UPDATEs match no rows and answer zero. An *absent*
one is a database no current build has ever booted, since every boot
opens and migrates all three chains unconditionally (`app.py:295-360`,
"Unconditionally, and behind no section at all"), and the API route runs
inside such a server. The one path that reaches a store without a server
is the CLI's local door, and on a database whose memory chain is not at
head the UPDATE fails, the transaction rolls back whole, and the
operator gets the storage refusal rather than a half-rename. That is the
honest outcome and it is what the atomicity pin already proves; probing
for the table first was considered and rejected, because a probe that
answers "no table" would let a rename report success while leaving
memory behind, which is the exact defect this issue exists to end.

**Imports rather than injection.** `config/store.py` gains two imports,
`memory.store` and `conversations.store`. Neither closes a cycle
(`memory.store` imports `config.loader`, `config.models` and
`conversations.store`; `conversations.store` imports `config.models`;
neither imports `config.store`), and neither reaches the CLI, whose import inventory
does not contain `config.store` at all
(`tests/unit/test_cli_import_weight.py:95-124`). The alternative,
handing the two functions in as parameters the way `app.py` hands
`purge_memory=purge` to the conversation store, exists to break a cycle
that is not here, and would put wiring in the composition root for a
fact that is not a choice: there is exactly one way to rewrite an agent
name in each store.

### What the rename is not

- **Not a document operation.** `import` is additive and names entries
  by their identity, so a rename is not expressible in it and none is
  added: a document says what should exist, and a rename is a thing that
  happens. An export taken after a rename carries the new name and
  imports back onto itself, which is the round trip the guide asks for.
- **Not visible to `diff` as a rename.** `config/reload.py:604-624`
  reports one removal and one addition, which is what the difference
  between a stored configuration and a running one honestly is: the
  running server is serving an agent that is gone and not serving one
  that arrived. Teaching the diff to recognize a pair as a rename would
  be a second encoding of an act the store already recorded, with
  nothing connecting the two.
- **Not an agent identifier.** The real end of name-keyed references is
  a stable id under every name, and it is a schema-wide change touching
  three chains, every listing and every event field. It is not this
  issue, and this issue does not make it harder: the rewrite is the same
  act an id would make unnecessary.

## Module layout

No new module, and the deletion test is why: every piece of this lands
beside the decision it belongs to.

- **`config/store.py` gains one repository verb**, `rename_agent(old,
  new) -> Renamed`, beside `apply` and the entity writes. It runs the
  same four phases every write runs (prepared outside the lock, staged
  inside it, checked once by `check_references`, persisted), which is
  what keeps a rename and a document from validating differently. The
  semantics belong here for the reason the module docstring gives:
  "Every semantic decision about the domain configuration lives here and
  not in the code that calls it."
- **`Renamed` is the frozen result**, beside `BoundDevice` and
  `Applied`: the two names, the MACs whose bindings moved, whether the
  default agent moved, and the two row counts. It is what the route
  reads to choose the boundary set and what the tests read to say what
  happened; nothing recovers either fact by re-reading the store.
- **`memory/store.py` gains `rename_owner`**, one statement under the
  chain's lock, beside `erase_facts` which it mirrors.
- **`conversations/store.py` gains `rename_agent`**, the same shape,
  beside the chain whose lock it takes. Not `threads.py`, which owns the
  reads that filter on the column: the chain is declared in the store
  and the store already imports `threads`, so the lock cannot be taken
  from there without a cycle. A chain module of its own was considered
  and refused by the deletion test: one authoritative definition already
  exists, in the module the guide says should hold it, and a third file
  holding one constant would hide nothing.
- **`conversations/store.py` gains the publication and the order's
  second holder**, beside `erased()` and `erasure_order()`: one function
  announcing a rename to the register of writers this process holds, and
  the comment above `_erasure_order` gains the rename as a second holder
  of the same rule.
- **`ConversationStore` gains the translation**, one per-session map and
  one resolution at the durable-write boundary, beside `_discard_dead`
  which is the same idea for the same reason, and popped where
  `_devices` is popped so it lives exactly as long as the session that
  needs it. `_write` resolves the name once
  per turn and hands it to both `_turn_row` and the `Landing`, so
  `_turn_row` takes the resolved name as an argument rather than reading
  it off the record: one value, two rows, no chance of the pair
  disagreeing. Not a module: it is a few lines in the object that
  already subscribes to what a store change publishes, and a module
  beside it would be a name that hides nothing.
- **`config/entities.py` gains one `Notice`**, the sixth, in the file
  that already owns the pairing of a sentence with the boundaries it
  announces.
- **`config/loader.py` gains one exception**, `AgentRenameConflictError`,
  beside the other refusals that are facts about the world, and
  `config/api.py`'s `REFUSAL_STATUS` gains its row. One class for the
  three destination states, since the correction is the same for all
  three.
- **`config/api.py` gains one route**, which makes one repository call
  and answers with what it did, per the handler contract already stated
  there.
- **`config/cli.py` gains one `Command` row and one `Act`**, plus one
  `declare` for the shape "one address, one payload word", and one
  payload field on `Invocation` beside `agents`, `file` and `pairs`.
  No new module: the design guide names a `config/cli_render.py` that
  exists only because `cli.py` is long as the counterexample, and #386's
  review round applied it to exactly this file three days ago.

**Two seams are new, and both are merged shapes reused.** The first
crosses into two foreign schemas and is stated as two function
signatures rather than as a shared object: each store owns its SQL, the
caller owns the transaction, and the lock order is a property of the
functions rather than of the call site. That is `purge`'s seam widened
by one store. The second is the publication, which crosses from the
write to whoever in this process is holding the old name, and it is
`erased()`'s seam carrying a different fact: a change to the store, an
order that covers the instant between the commit and the announcement,
and a holder that decides for itself what the announcement means for a
row already on its way.

## Documentation footprint

Named by role, per [`docs/README.md`](../README.md)'s taxonomy.

**Generated references, which move through their generators, never by
hand:**

- `docs/reference/api-openapi.json`, in M3: the new route, its request
  body model and its responses. Regenerated with
  `uv run vinga-server config openapi > ../docs/reference/api-openapi.json`.
- `docs/reference/domain-config.md`, in M3: the agent descriptor's note
  is where the orphaning caveat lives
  (`config/entities.py:526-532`), and it is rewritten to say what a
  rename now does. This is the caveat the issue set out to remove, and
  it is generated, so the change is to the descriptor.
- `docs/reference/cli.md`, in M4: the generated half grows the new
  command's help page.
- `vinga-server/tests/unit/command-spellings.txt`, in every milestone:
  the manifest records physical line positions across every tracked
  file, so this plan's own document and the CHANGELOG entries stale it
  whatever the code does. Regenerated with
  `uv run python -m tests.unit.test_command_spellings` before the unit
  lane.

**A maintained map**, `vinga-server/README.md`, in M3: two paragraphs
state the orphaning as a standing fact, at `:855-857` (the listings
answer owners nothing is configured under, "renaming an agent orphans
what it remembered") and at `:3159-3161` (the memory is the one thing an
apply does not move). Both become false in this milestone and are
rewritten in it.
`docs/architecture/observability-surfaces.md:37` carries the same claim
in half a sentence and moves with them.

**Two schema comments, which need two migrations.** `memory.facts.owner`'s
column comment says "Renaming an agent orphans its rows, exactly as it
orphaned its file" (`memory/schema.py:103-112`), and it is committed DDL:
`2002_memory_scopes` set it, and
`test_the_baseline_builds_exactly_what_the_tables_declare` compares the
built schema with the declared metadata through Alembic's own
autogeneration. Alembic 1.18.5 compares column comments by default
(`alembic/autogenerate/compare/comments.py`), so editing `schema.py`
alone fails that test, and a migrated database would keep saying
something false either way. So M3 carries `2003_rename_moves_memory`,
down-revision `2002_memory_scopes`, altering that one comment and
nothing else. It is the smallest honest migration and the CI wheel step
exercises it.

`record.conversations.agent`'s comment is the second, and it is a
contract rather than an aside: it says the thread has one agent "and the
only one it will ever have" (`conversations/schema.py:283-293`), which
is what this plan's premise correction falsifies. What stays true is the
sentence's real subject, that a conversation is a dialogue with exactly
one agent and a handover starts a second thread; what changes is that
the name that agent is filed under can be rewritten by a rename, while
the dated columns beside it keep the name of the moment. M3 carries
`1003_rename_moves_thread_ownership` on the record chain, down-revision
`1002_conversation_threads`, altering that one comment, and
`docs/reference/conversations-schema.md` regenerates from it through
`vinga-server conversations schema`, since that page is rendered from
these comments (`conversations/docgen.py:1-10`).

**Docstrings that are the contract at their own surface**, in M3:
`memory/api.py:453-459` (the owners listing explains itself by the
orphaning) and `:630-633` (erasing an agent's memory is "the verb for an
orphan the listings turned up: a renamed agent's rows have no other way
out"). Both stay true for a *deleted* agent and stop being true for a
renamed one, so both are corrected where they are written.

**Dated execution records:** `CHANGELOG.md` gains an entry per
milestone, and this plan's companion implementation doc gains a section
per milestone in the change that ticks it.

**Not touched, and stated so a reviewer does not go looking:**
`docs/reference/events.md`,
`config.example.yaml` and the preset examples (none of them documents
the caveat; the orphaning text the issue remembers as living in the
example configuration moved into the agent descriptor's note when memory
moved into Postgres), and `docs/concepts.md`, whose memory section
describes scopes rather than renaming.

## Tests

Reusing the assets that exist wherever the assertion already has a home.

- **The sentinel sweep, which is the plan's central pin.** Build a store
  holding one agent under a sentinel name, referenced from every live
  place at once: two device bindings, the default agent, memory facts
  including one held (forgotten) row, and two conversation threads with
  turns. Rename it, then read every row of every table of the three
  schemas, render each value as text, and assert that the set of
  `(table, column)` pairs still carrying the sentinel is exactly the
  recorded one, which is the dated record and nothing else. Written as
  an equality against a recorded set rather than as five assertions
  about five columns, so it fails in both directions: a live reference
  left behind, and a dated column that stopped carrying the name it is
  supposed to keep.
  **What it does and does not promise, stated because the sweep is
  where an inventory claim would be tempting.** It is a fact about
  values, not about the schema: it catches any column this fixture
  populates, including one added later, and it cannot see a column
  nothing in the fixture writes to. So the guarantee is that the
  references enumerated today are covered and that a new column reached
  by this fixture is caught; a new column with no fixture behind it is
  covered by the same review this plan's census was, not by a test that
  cannot know which text field holds a name.
- **The inventory pin**, so the sweep cannot silently stop covering the
  domain half: after the rename, `check_references` over the stored
  snapshot is empty, and the set of sections it walks is read from the
  function rather than restated.
- **The in-flight cases, with the interleaving forced rather than
  hoped for.** The two-writer arrangement #314 built and #328 hardened is
  what drives them: a session writing turns while a rename commits, run
  twice, once with the thread already materialized and once with its
  first turn arriving after the rename. The materialized case asserts
  every turn is stored and none is dropped, and that the thread's agent
  is the new name; the un-materialized case asserts the row it created
  carries the new name, which is the assertion that fails today by
  creating a fresh detached reference. A third case takes the ordering
  lock's own claim: a durable batch cannot commit between the rename's
  commit and its publication, driven by a gate in that instant the way
  the erasure suite drives its own.
- **The translation's own pins, asserted on the rows rather than on the
  thread.** A landing for an agent nothing renamed is untouched; a
  chained rename resolves in one step, so a landing under the first name
  lands under the third; and the post-rename turn is read back whole,
  asserting `turns.agent` and every `legs[].agent` carry the new name
  while the turn committed before the rename still carries the old one
  and the session row carries the name it opened with. Read back rather
  than inferred from the thread's owner, because a landing-only
  translation passes a thread-owner assertion and writes exactly the
  disagreeing row this pin exists to catch.
- **The reused name, which is the case a process-wide map would fail.**
  Rename, then create an agent under the freed old name, then open a
  session on it while the earlier session is still draining: the new
  session's turns and thread carry the name it was opened with, and the
  draining one's still translate. It is the pin the lifecycle decision
  rests on, so it is written as a case rather than as a sentence.
- **The competing write, between the check and the update.** A second
  writer adds a fact under the destination name while a rename is
  between its memory check and its memory update, driven by the
  two-writer arrangement #314 built: the writer queues on the chain lock
  the rename is holding, and the rename's decision is still true when it
  writes. Run once per store that checks a destination, since the claim
  is about the lock rather than about the table.
- **The lock-order pin joins the one #83 already has.**
  `docs/plans/2026-08-30-memory-scopes.md` records a test walking the
  erasure and retention paths and asserting that every transaction
  taking both chains' locks takes them in ascending key order. The
  rename's transaction joins that walk as a third path, taking all three
  keys.
- **Atomicity, driven from the last statement.** With the memory rewrite
  forced to fail, the whole rename rolls back: the agents row, the
  bindings, the default agent and the threads are all as they were, and
  the refusal is the classified one rather than a driver message.
  Reverting a fix in place to watch a sentinel appear is the repository's
  standing way of proving a pin bites, and it applies here (copy the
  file aside and `touch` it on the way back, never `git checkout`, per
  `AGENTS.md`).
- **The refusals, seven cases, one per state**, asserting the fixed
  sentence and, for the three destination states, that neither name
  appears in it. Each collision
  case builds the destination in one store and leaves it empty in the
  other two, so a check that stopped running is a failure rather than a
  case that passes for the wrong reason: an agent under the new name, a
  memory fact under it with no agent, and orphaned threads under it with
  neither.
- **The no-leak cases, on the surface each can actually be reached
  through.** Two of them, because the two names arrive by different
  doors.
  - *The caller's name*, which is the reachable one and the one a paste
    lands in: a new name carrying a URL credential, sent through the
    route and typed at the verb. It is refused, since such a URL holds a
    slash and `_check_addressable` refuses one, and the assertion is
    that it appears in no response body, no header, no log record, no
    stdout and no stderr. This is the case the surface has, and it is
    the one the standard is about.
  - *The stored name*, which cannot be reached through the route at all:
    a name holding a credential holds a slash, so no path segment
    addresses it, which `config/views.py:127-144` already measures for
    the reads. So the strip on the old name is pinned where it lives, as
    a unit test of the line composer over a planted stored name, with
    the unreachability stated in the test rather than left as an
    implication. It is belt and braces exactly as #382 says the same
    strip is on the write path, and a plan that pretended otherwise
    would have shipped a test that cannot be written.
- **The boundary arms, twice, once per side of the wire.** In the
  milestone that adds the route they are asserted on what the route
  answers: a rename that moved only the row carries the apply notice and
  `(reload,)`; one that moved a binding or the default agent carries the
  new sentence and `(reload, check-in)`; a snapshot-only server carries
  the store-boot arm. In the milestone that adds the verb the same three
  are driven end to end through the registered command, which is where a
  client assertion can honestly be made: the request it sends is a POST
  to `/agents/{name}/rename` carrying `{"to": ...}` and nothing else,
  and its stderr is the server's sentence with this client's remedy
  under it, one per arm. Split this way because the `Act` arrives with
  the verb and `Act.read()` validates an answer rather than rendering
  one, so a rendering assertion in the earlier milestone would be
  asserting a client that is not there yet. The producer-side pin from
  #386 covers the sixth notice for free, since it asserts every
  `Notice.applies` member is an `Applies` member and no
  `Notice.sentence` contains `PROGRAM`.
- **The reversibility claim is a test rather than a sentence**, because
  it is what the confirmation decision rests on: rename and rename back,
  and the store, the memory rows (held ones included) and the threads are
  byte-identical to what they were. It runs a second time with a
  stranger present: memory rows and threads under a third name that was
  never involved are untouched by both renames, which is the assertion a
  merge would fail even though the round trip on its own would pass.
- **Existing suites that translate rather than grow**:
  `test_config_api_writes.py` gains the route's cases beside the other
  writes; `test_memory_store.py` and `test_memory_api.py` gain the owner
  rewrite beside `erase_facts`; `test_config_cli_respelling.py` gains one
  licensed substitution for the new stderr text; the command-spellings
  census covers the new CLI sentence with no new code, which is the
  property #386 bought.
- **The contract check needs no editing**: `test_api_contract.py` reads
  the committed OpenAPI document as bytes and holds every declared act
  against it, so regenerating the document is what keeps it green.

## Risks

- **The record column is a scope change against the issue's own text.**
  Mitigated by measuring rather than arguing (two WHERE clauses, zero for
  `turns.agent`), by keeping the rest of the record untouched, and by
  putting the finding in front of the plan review rather than inside a
  milestone. If the review or the issue's owner refuses it, the fallback
  is to leave `record.conversations.agent` alone and file the detached
  history as its own issue; the rest of this plan is unchanged either
  way, which is why the record rewrite is one function call and one pin.
- **A rename holds three advisory locks while it updates rows.** The
  work is bounded by one agent's bindings, facts and threads, and both
  UPDATEs are index-driven (`ix_facts_scope` on `(scope, owner, id)`,
  `ix_conversations_agent_activity` on `(agent, last_active_at, id)`).
  A contended database answers the retryable refusal every other write
  answers, which is `LOCK_TIMEOUT_MS` doing its job rather than a new
  failure mode. Worth naming because it is the first transaction in the
  server that can hold all three at once.
- **The middle boundary arm is chosen by what was rewritten**, so a
  future rewrite target added without extending that choice would
  announce too little. Held by the result type: the arm reads
  `Renamed`'s own fields, so a new field with no reader is visible in
  review rather than silently ignored.
- **A migration whose whole content is a comment** looks like churn.
  Priced above with the test that fails without it and the database that
  keeps lying without it, and it is forward-only, so the standing
  compatibility promise is untouched.
- **The census manifest stales on this plan's own files.** It does,
  every time; regenerate through its module before the unit lane.
- **The window that stays leaves orphans nothing sweeps.** The memory
  sweep takes conversation-state orphans, not agent ones
  (`memory/store.py:150-155`, `schema.py:217`), so a fact a live session
  writes under the old name stays until an operator deletes it. Stated
  in the docs the rename rewrites, and the listing is where it shows.
- **The in-flight protocol is process-local, and the break-glass door is
  outside it.** A rename typed through the local door against a database
  a running server is using is not ordered against that server's writer,
  because the order and the register are objects in one process, exactly
  as the erasure's are. Mitigated by what that door is for, a deployment
  whose server will not start, and stated where the protocol is
  described rather than left to be discovered by whoever tries it.
- **The translation is a second place a name is interpreted**, and it
  earns that by closing a data loss rather than a cosmetic mismatch. The
  bound is written into the design: one map per live session, in the
  object that already subscribes to a store change, resolved once at the
  one boundary where a name enters the record, composed on insert so a
  chain of renames never becomes a walk, and retired with the session's
  own state so a name freed by a rename and reused later is never
  translated for the agent that has it now.

## The standing lenses, answered

Each is answered where the territory touches it, and each answer names
where it is enforced rather than asserting it.

- **No leak.** Three surfaces, one rule. A refusal names the rule and
  never the value, which is `check_references`'s shape already; the new
  name is caller text on both sides of the API and is echoed in nothing;
  the old name is a stored identity, so it may be spoken and goes
  through `without_url_credential` first, per
  [the converged location policy](../features/2026-09-05-boot-refusal-location-policy.md).
  The one place a name is printed after a successful rename is the
  acknowledgement's line, composed from the transaction's own result and
  stripped, pinned by a unit test over a planted stored name because the
  route cannot address one; the reachable no-leak case is the other
  door, a credential-bearing name typed as the new one.
- **Closed sets at decision sites.** Two decision sites, both closed and
  both pinned. The refusals are a seven-state set, each state a
  condition of the transaction before it writes: one absent source,
  three occupied destinations, two malformed new names and one
  contended database. The boundary is the `Applies`
  vocabulary #386 landed, chosen in three arms from `Renamed`'s own
  fields, with the producer-side pin that every `Notice.applies` member
  is an `Applies` member already in place. Nothing branches on a
  substring or on a sentence's wording anywhere in this change.
- **Honest seams.** The crossing into two foreign schemas is two
  function signatures, each owned by the store whose SQL it is, each
  taking its own chain's lock so that the ordering is a property of the
  function. No store holds a reference to another, no shared mutable
  object crosses, and the caller owns the transaction. The result type is
  the seam back: what the write did travels as fields rather than being
  recovered by re-reading the store. The second crossing, into the
  writers this process is holding, is the erasure's published fact
  carrying a different one: announced after the commit and inside the
  order, and read by a holder that decides for itself what it means for
  a row already on its way.
- **Tooling-backed inventories.** The domain half's references are
  `check_references`'s own walk rather than a list in this plan; the
  tables are read off the three metadata objects; the live-versus-record
  line is drawn by grepping for filters rather than by reading intent;
  and the sweep asserts, as an equality against a recorded set, which
  `(table, column)` pairs still carry a sentinel after a rename. What
  that is worth is bounded and the plan says so: it is a claim about
  values a fixture wrote, not a claim that the schema can name its own
  agent references.
- **Pin before reshape.** M1 and M2 add behavior behind no reachable
  surface and pin it whole, the transaction and then the protocol,
  including atomicity, reversibility and the forced interleavings,
  before M3 gives any of it a door. The documents that describe the old behavior are
  corrected in the milestone that makes them false, and the one
  committed artifact that would otherwise drift silently, the column
  comment, moves through a migration that a merged test already holds.

## Milestones

- [x] **[M1: one transaction, three schemas](2026-09-05-agent-rename-implementation.md#m1-one-transaction-three-schemas)** (PR #415).
  `memory.store.rename_owner` and `conversations.store.rename_agent`,
  each taking the caller's connection, its own chain's lock as its first
  statement, and raising a classified failure; `ConfigStore.rename_agent`
  running the four phases and returning `Renamed`; the seven refusals as
  fixed sentences, each destination check run under its own store's lock
  in the transaction that would write and raising the typed conflict
  through each store's classifier; `AgentRenameConflictError` with its
  409 row and the pin on the mapping; the sentinel sweep, the inventory
  pin, the three collision cases, the atomicity
  pin, the reversibility pin with a stranger present, and the third path added to the
  lock-order walk. No route and no CLI, so nothing an operator can reach
  changes and main stays releasable; the risky half, which is the
  cross-schema transaction, sits alone in its own review. Design
  footprint: deepens `config/store.py` (one verb whose caller learns
  nothing about three schemas), `memory/store.py` and
  `conversations/store.py` (one function each, beside the ones they
  mirror); one new frozen result type beside the two that exist; no new
  module and no new seam beyond the two signatures. Documentation
  footprint: `CHANGELOG.md`, a dated execution record; the census
  manifest; the implementation-doc section.
- [x] **[M2: the order that covers the sessions in flight](2026-09-05-agent-rename-implementation.md#m2-the-order-that-covers-the-sessions-in-flight)** (PR #417). The rename
  enters `erasure_order()` before it opens its transaction and publishes
  after it commits, still inside the order, through a sibling of
  `erased()` that reaches the same register of writers; the comment
  above `_erasure_order` gains the rename as its second holder; the
  conversation writer keeps the per-session map and resolves the name
  once at the durable-write boundary, using that one value for the turn
  row, its legs and the landing, and leaving the session row's own
  columns verbatim; the forced-interleaving cases for a
  materialized and an un-materialized thread, the case that proves a
  durable batch cannot commit between the commit and the publication,
  and the translation's own three pins; the follow-up issue for the
  memory store's untranslated window, naming the same publication as its
  hook. Still no route and no CLI, so the protocol lands whole before
  anything can call it, which is what keeps a half-built handoff from
  ever running; producer and consumer are one milestone for the reason
  #386 gave, that a tolerance arriving a milestone later is a refusal in
  between. Design footprint: one publication and one order beside the
  ones they copy, one map inside the writer that already subscribes to
  a store change; no new module.
- [x] **[M3: the route, the boundary it announces, and the caveat it
  retires](2026-09-05-agent-rename-implementation.md#m3-the-route-the-boundary-it-announces-and-the-caveat-it-retires)**
  (PR #418). `POST /agents/{name}/rename` with its request model and
  `Acknowledgement` answer; the sixth `Notice` and the three-arm choice;
  the acknowledgement's line composed from `Renamed` with the old name
  stripped; the route's cases and the three boundary arms asserted on
  what the route answers;
  `2003_rename_moves_memory` altering the `facts.owner` comment and
  `1003_rename_moves_thread_ownership` altering
  `conversations.agent`'s, each with its `schema.py` moved in the same
  commit, and `docs/reference/conversations-schema.md` regenerated;
  the agent descriptor's
  note rewritten, which moves `docs/reference/domain-config.md`;
  `docs/reference/api-openapi.json` regenerated; the two README
  paragraphs, the observability line and the two `memory/api.py`
  docstrings; a CHANGELOG `Added` entry; the census manifest; the
  implementation-doc section. Behavior becomes reachable here and sits
  alone in this review, and every document that this makes false is
  corrected in the same change rather than in a later tidy-up, which is
  the rule #386 settled. Design footprint: one route making one
  repository call; `entities.py` deepens by one notice; no new module.
- [x] **[M4: the verb](2026-09-05-agent-rename-implementation.md#m4-the-verb)**
  (PR TBD). `vinga agent rename <old> <new>` as one
  `Command` row with one `Act`, `destroys=False` with the reasoning in a
  comment on the row; the payload field on `Invocation`; the `declare`
  for one address and one payload word; the end-to-end cases through the
  registered command, one per boundary arm, asserting the request it
  sends and the two lines it prints; `docs/reference/cli.md`
  regenerated; the licensed substitution in the respelling suite; the
  census manifest; a CHANGELOG `Added` entry; the implementation-doc
  section. The client half lands alone, so the review sees the terminal
  output it adds and nothing else. Design footprint: no new module, one
  row in the table everything else about a command is read from.

## Plan review round

Backend codex, model `gpt-5.6-sol`, 2026-09-05, against commit
`064607a1`; the reviewer ran 6m01s. Verdict: NOT READY, 2 P1, 6 P2 and
1 P3.

1. **P1: destination conversation rows are an unrefused, irreversible
   merge.** The plan refuses a destination collision in `domain.agents`
   and in `memory.facts` and not in `record.conversations`, which has no
   foreign key and whose rows survive the agent that wrote them
   (`conversations/schema.py:257-294`). Threads already recorded under
   the new name are therefore merged into the renamed agent's history,
   and a rename back moves them too, so the reversibility the
   confirmation decision rests on does not hold. Add a destination
   conversation collision to the closed refusal set, checked under the
   record chain's lock in the same transaction that would update; answer
   409 without naming caller text; document the remedy; test a
   destination holding orphaned threads and test that a rename back
   leaves them where they were.

   *Resolution*: accepted in full, and the premise checked before
   accepting: `record.conversations` carries no foreign key, an unbound
   agent can be deleted while its threads stay, and retention prunes by
   idleness rather than by ownership, so a destination holding orphaned
   threads is an ordinary state rather than a constructed one. The plan
   now states the rule the three checks come from as one sentence, that
   the destination name is free everywhere this rename would write, and
   says why it is one rule: a rename may never merge two pasts, because
   no second rename can tell them apart afterwards. The refusal set is
   seven, the thread collision carries its own note with the remedy and
   the #410 class it joins, the confirmation section's reasoning now
   rests on the destination rule rather than on the memory half of it,
   each collision case builds the destination in one store and leaves it
   empty in the other two so a check that stopped running fails, and the
   reversibility pin runs a second time with a stranger present, which
   is the assertion a merge fails while a plain round trip passes.

2. **P1: renaming a thread's agent breaks the sessions in flight on it.**
   `threads.landed()` raises `MisattributedTurn(ANOTHER_AGENT)` when an
   arriving turn's agent does not match the stored `conversations.agent`
   (`threads.py:261-294`), and the durable writer answers that exception
   by dropping the marker's whole batch
   (`conversations/store.py:1038-1074`), so a live session's later turns
   are lost. A thread that has not materialized yet takes the other
   branch and INSERTs a fresh row under the old name, recreating exactly
   the detached live reference the rename set out to remove. Define an
   ordering and handoff protocol covering stale conversation writers
   rather than only the lock order inside the rename, prevent both the
   mismatch drop and the old-name insertion, and test both with forced
   interleaving.

   *Resolution*: accepted in full, and both branches reproduced by
   reading before amending: `landed()` refuses on a name mismatch and
   the durable writer answers a non-busy exception by failing the whole
   batch, while the other branch INSERTs from the landing and so writes
   a fresh row under the old name. The protocol is not invented for this:
   a thread erasure has the identical hazard, and
   `conversations/store.py:270-320` already states it, orders it with
   `_erasure_order` outside every chain lock, and publishes to a
   register of writers after the commit and inside the order. The rename
   takes the same lock in the same position, publishes a rename through
   a sibling of `erased()`, and the conversation writer translates a
   landing's agent before `landed()` reads it, with the map composed on
   insert so a chain of renames stays flat. Both defects close together:
   the materialized case matches and is stored, the un-materialized case
   inserts under the new name. What the translation decides rather than
   preserves is written down, that a turn spoken after the rename
   carries the new name in the dated column, which is the column saying
   what was true when. The plan gains a section for the protocol, the
   forced-interleaving cases with the two-writer arrangement #314 built,
   the pin that no durable batch can commit between the commit and the
   publication, the module-layout entries, and a milestone of its own:
   the cut is now four, with the protocol landing whole before anything
   can call it. The memory store is deliberately not a second
   subscriber, with the asymmetry argued where the remaining window is
   stated and a follow-up filed in the milestone that ships this.

3. **P2: the record helper cannot take its chain's lock from
   `threads.py`.** `CONVERSATIONS_CHAIN` is declared in
   `conversations/store.py`, which already imports `threads`, so
   importing it back closes a cycle. Put the locking seam in
   `conversations/store.py`, or move the chain declaration to a
   dependency-neutral owner; a small chain module passes the deletion
   test if more than one caller needs one authoritative definition.

   *Resolution*: accepted, with the first of the two options, and the
   third refuted. The cycle is real (`conversations/store.py:101`
   imports `threads`), so the locking seam moves into
   `conversations/store.py`, beside the chain it takes and beside the
   caller of `purge`, which also makes the record half the mirror of the
   memory half: `purge` and `erase_facts` live in `memory/store.py` for
   the same reason, that a chain is a fact of the store that owns it
   (`db/__init__.py:24-27`). A chain module of its own is refused by the
   deletion test rather than adopted: there is one authoritative
   definition already, in the module that owns it, and the second caller
   is a function this plan is putting in that same module. The statement
   it issues reaches `threads`' table through the shared metadata, which
   imports nothing new. The module layout, the seam description, the
   import note and M1's deliverables all say `conversations.store` now.

4. **P2: `rename_owner`'s advertised signature cannot enforce the memory
   collision.** Nothing in the schema prevents the merge, and an `int`
   cannot distinguish a destination that already has rows from a source
   that has none. Have it take the memory lock, check the destination,
   raise the typed rename conflict untranslated and then update, or
   answer with a result type that reports the collision separately. Pin
   a competing memory write between the check and the update.

   *Resolution*: accepted, with the first of the two options. The
   finding is right that no constraint expresses the rule and that a
   count cannot carry the distinction, and it is right for the record
   half too, so both functions are now check then update under their own
   lock inside the transaction that would write, raising the typed
   conflict and answering the count only on the path that renamed. The
   result type was not taken: a raise is what every other refusal in
   these stores does, and a caller that had to branch on a result field
   would be the one place in the write path where a refusal is a value.
   The plan says why the check cannot go stale, which is that the lock
   being held is the one every writer of that chain takes at BEGIN, and
   the competing-write pin asserts it rather than assuming it: a second
   writer's `add` under the destination name is made to run between the
   check and the update and is shown to queue behind the lock, using the
   two-writer arrangement #314 built. The conflict travelling
   untranslated is stated with its two merged instances, `_written`'s
   `except _Refused` arm and `_transaction`'s `except ConfigError`.

5. **P2: the promised 409s have no exception type and no mapping.**
   `REFUSAL_STATUS` maps a plain `ConfigError` to 422 and only the
   listed subclasses to 409 (`config/api.py:307-342`), and no milestone
   names one. Add a dedicated rename conflict exception in
   `config/loader.py`, map it to 409 in `config/api.py`, and keep it
   intact across the sanitizing boundaries of all three stores.

   *Resolution*: accepted in full, and the mapping checked: only the
   listed subclasses reach 409 and the fallback row is 422, so the plan
   was promising a status the code would not have produced. One class,
   `AgentRenameConflictError`, lands beside `DeviceAlreadyBoundError`,
   which is the same kind of fact about the world rather than about the
   request, with its `REFUSAL_STATUS` row in the same milestone that
   raises it and a test asserting the mapping. One class for the three
   destination states rather than three, because the correction is the
   same for all three and the sentence is what differs. Keeping it
   intact across the boundaries is the amendment to finding 4: it is a
   `ConfigError` subclass, so `_transaction`'s re-raise and the memory
   store's refusal arm already pass it, and the plan says so where the
   two functions are described.

6. **P2: the record schema's own contract goes stale.**
   `conversations.agent`'s column comment says the thread has one agent
   "and the only one it will ever have" (`conversations/schema.py:283-293`),
   which this plan's premise correction falsifies. Update the comment to
   say the rename rewrites the current owner's name while the dated rows
   are unchanged, add the forward migration on the record chain, and
   regenerate `docs/reference/conversations-schema.md`.

   *Resolution*: accepted in full, and it caught a claim the plan had
   made in the other direction: the documentation footprint said
   `conversations-schema.md` was untouched because only rows move, which
   is false the moment the comment above those rows stops being true.
   The comment's real subject survives, that a conversation is a
   dialogue with exactly one agent and a handover starts a second
   thread; what changes is that the name it is filed under can be
   rewritten while the dated columns keep the name of the moment. M3
   carries `1003_rename_moves_thread_ownership` on the record chain,
   down-revision `1002_conversation_threads`, and the page regenerates
   through `vinga-server conversations schema`, which renders it from
   these comments. The two migrations are named together in the
   footprint, and the same test that forces the memory one forces this
   one.

7. **P2: the milestone that adds the route cannot run the client tests
   it claims.** The rename's `Act` arrives with the CLI, a milestone
   later, and `Act.read()` validates an answer rather than rendering it.
   Test the API-produced notice and `applies` directly in the milestone
   that adds the route, and put the end-to-end registered-command cases
   in the milestone that adds the verb: the POST path, the raw
   `{"to": ...}` body, the acknowledgement's rendering, and the remedy
   for each of the three boundary arms.

   *Resolution*: accepted in full. The plan had lifted #386's test shape
   without noticing that the shape belongs to a milestone that has an
   `Act` to read with, and this one does not until the verb lands. The
   boundary arms are now asserted twice, once per side of the wire: on
   what the route answers in the milestone that adds it, and end to end
   through the registered command in the milestone that adds the verb,
   where the request's method, path and body and both printed lines are
   the assertion. Both milestones' deliverables say which half they
   carry.

8. **P2: the credential-bearing old name cannot be reached through the
   route.** A URL carrying a credential contains a slash, and a
   path-segment route cannot address such a name, which the existing
   sentinel already proves. Make the sanitizing of a legacy stored
   identity a focused unit test of the formatter with the
   unreachability stated, and make the reachable no-leak tests use
   credential-bearing caller text as the NEW name, asserting it appears
   in no body, header, stdout, stderr or log line.

   *Resolution*: accepted in full, and checked against the merged
   measurement rather than reasoned about: `config/views.py:127-144`
   already records that such a name is unaddressable before a change and
   after it, which is the same fact this route inherits. The plan's
   single no-leak case becomes two, one per door. The reachable one uses
   a credential-bearing NEW name, refused for its slash, asserting the
   value appears in no body, header, log record, stdout or stderr. The
   unreachable one keeps the strip pinned where it lives, as a unit test
   of the line composer over a planted stored name, with the
   unreachability stated in the test rather than implied, which is the
   same belt-and-braces posture #382 records for the write path.

9. **P3: the sentinel sweep does not deliver the inventory guarantee it
   claims.** Metadata cannot say which text or JSON fields hold an agent
   name, so the sweep cannot promise that a reference added later fails
   a test. Narrow the claim to the references enumerated today, or
   introduce an authoritative per-store registry of live agent
   references that both the rename and the coverage test read. Choose
   with the design guide in hand: two structures that must agree are one
   structure with a bug pending, so if a registry is the answer, the
   rename itself has to read it.

   *Resolution*: the claim is narrowed, and the registry is refused with
   the guide's own test rather than on effort. A registry would have to
   be read by the rename to be one structure rather than two, and the
   rename cannot read one: the domain half is rewritten through the
   models and the staging path, where the references are a JSON array's
   elements and a key in a settings table rather than columns, so a
   column registry would drive the two easy stores and leave the hard
   one restating itself. That is a registry with one real consumer, the
   test, which is the second encoding the guide is warning about rather
   than the fix for one. So the sweep keeps its shape and loses its
   overclaim: it is an equality against a recorded set of
   `(table, column)` pairs, it fails in both directions, and the plan
   states what it is a claim about, which is values a fixture wrote
   rather than a schema that can name its own agent references. A new
   column with no fixture behind it is caught by review, which is what
   caught this plan's own census.

## Plan review round, the delta

Backend codex, model `gpt-5.6-terra`, 2026-09-06, against the amended
tip `2758a9a8`; the reviewer ran 2m04s. Verdict: ready after the P1/P2
amendments. 1 P1, 1 P2 and 1 P3.

1. **P1: translating the landing alone cannot produce the turn record
   the plan promises.** `ConversationStore._write` inserts the turn row
   before it builds the `threads.Landing`, and `_turn_row` takes the
   agent straight off the record (`conversations/store.py:1234-1264`,
   `:1342-1365`), so a translation applied at the landing leaves
   `turns.agent` carrying the old name while the thread carries the new
   one, which is not what the plan's own pin asserts. Translate the
   writer's agent once at the durable-write boundary and use that one
   value for both the turn row and the landing; say which other dated
   fields stay verbatim, `legs[].agent` included; and make the
   post-rename pin assert the row that was written rather than only the
   thread's owner. Note that this qualifies the "dated record stays as
   written" line for the post-rename turns of an in-flight session, and
   the plan should state the distinction crisply: rows written before
   the rename are dated record and stay, and rows written after it by a
   stale writer are new writes carrying the current name.

   *Resolution*: accepted in full, and reproduced by reading before
   amending: `_write` inserts the turn and then builds the `Landing`,
   and `_turn_row` reads `record.agent`, so the plan as written would
   have filed a turn under the old name onto a thread under the new one.
   The resolution moves to the durable-write boundary: `_write` resolves
   the name once per turn and hands that one value to `_turn_row` and to
   the `Landing`, so `_turn_row` takes it as an argument rather than
   reading the record. `legs[].agent` is translated with it, because a
   row may not disagree with itself and a leg is written by the same
   insert at the same instant. `sessions.agent` and `sessions.agents`
   stay verbatim even when the opening insert lands after the rename,
   deliberately: that column's subject is the moment the session opened,
   which is before the rename. The plan now states the line as three
   cases in a table, with the crisp version the finding asked for above
   it: a row already written is dated record and is never touched, a row
   written after the rename by a stale writer is a new write carrying
   the current name, and a column whose subject is an earlier moment
   keeps that moment's name. The pin reads the row back rather than the
   thread's owner, which is what a landing-only translation would have
   passed.

2. **P2: the refusal set is counted two ways.** Seven in the closed-set
   section and in M1, "six-state" in the standing-lenses answer. Pick
   one classification and use it everywhere, the test inventory
   included.

   *Resolution*: accepted. Seven is the count, and the six was a
   leftover from before the thread collision joined the set rather than
   a second classification anybody meant. The lens now says seven and
   spells out which they are (one absent source, three occupied
   destinations, two malformed new names, one contended database), and
   the test inventory says seven cases, one per state, so the three
   places that count them agree and a reader can check the arithmetic
   against the table.

3. **P3: the writer's rename map has no lifecycle bound.** "Bounded by
   the number of renames in one process's lifetime" is not a bound for a
   long-running process. Resolve it deliberately: either state that the
   map persists for the process's lifetime as a small
   operator-controlled accumulation, with the size argument written
   down, or define a session-tied retirement that still preserves the
   translations a queued durable batch needs. Take the simpler
   defensible one and record why.

   *Resolution*: session-tied retirement, and the process-lifetime
   option is refused for correctness rather than for size, which is the
   part the finding did not have to argue and the plan now does. A
   process-wide map is wrong on a case the grammar allows: a rename
   frees the old name, an operator may create a new agent under it, and
   a stale entry would file that new agent's turns under the renamed
   one. So a publication marks the sessions live at that instant and no
   others, and the entries are popped where `_devices` is popped
   (`conversations/store.py:845-853, 950-958`), which is after a close
   has been committed rather than when it arrives, so a queued batch
   still finds its translation and nothing outlives the session that
   needed it. No new lifecycle and no timer: the retirement rides one
   the writer already runs. The reused-name case joins the tests as the
   pin this decision rests on.
