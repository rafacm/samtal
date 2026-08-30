# Domain concepts

**Date:** 2026-08-27

The domain model of vinga from the user's point of view: the nouns, how
they relate, and the semantics that were decided on purpose. This is a
maintained map, and it is deliberately ahead of the code. Some of what
it describes runs today and some is direction that was decided but not
built, so every section opens by saying which, and a claim that differs
from its section says so where it stands.

What outranks this page, and on what:

- [**The product promises**](architecture/product-promises.md) are the
  commitments this model must keep. Where the two touch, the promise is
  cited here rather than paraphrased.
- [**The guidelines**](architecture/guidelines.md) and
  [**the decision records**](adr/README.md) hold how the code keeps
  them. This page says what the user gets; those say how vinga is built
  to give it.
- **The owning issue or record holds a decided direction.** Direction
  belongs to whatever decided it, and each one below cites its owner.
  This page is not itself a decision record: where a direction has no
  owner, its status line says exactly that, so a reader can tell a
  settled decision from a sentence written here.
- **The generated references hold exact current behavior.** The
  [domain configuration reference](reference/domain-config.md) and the
  [conversation store schema](reference/conversations-schema.md) are
  rendered from the code and diffed by CI, so they cannot describe a
  server this repository does not build. Where this page and one of
  them disagree about what the server does now, the reference is right.
- [**The Xiaozhi notes**](xiaozhi-notes.md) hold the protocol and the
  wire. Mechanics are linked from here, never restated.

The [glossary](glossary.md) defines each term in one paragraph for
looking things up; this page explains how the terms fit together and
why.

## On this page

- [The model in one paragraph](#the-model-in-one-paragraph): every noun
  and its status, in six sentences.
- [Device](#device): the hardware, what it declares, and what it
  reports.
- [Agent](#agent): what answers, and why the word is not persona.
- [Binding](#binding): which agents a device can reach, and which one
  answers a fresh wake.
- [Conversation and session](#conversation-and-session): the
  load-bearing distinction, the stored record, and what the durable
  thread can be asked to do next.
- [Configuration changes arrive as whole
  worlds](#configuration-changes-arrive-as-whole-worlds): when an edit
  reaches a conversation in progress.
- [The wake word wakes the device, not an
  agent](#the-wake-word-wakes-the-device-not-an-agent): the doorbell,
  and why per-agent wake words cannot exist.
- [Memory](#memory): what an agent keeps, and what it is keyed by.
- [Meta capabilities](#meta-capabilities): the questions every
  conversation must be able to answer.
- [The help agent](#the-help-agent): the built-in agent that explains
  the device and the system.
- [Before users arrive](#before-users-arrive): the named limitation the
  user slot leaves behind.

## The model in one paragraph

**Implemented today, with the exception marked inline.**

A **device** is a physical endpoint with no intelligence of its own. An
**agent** is a named unit of behavior: a system prompt, a model
configuration, a voice, and a set of MCP tools. A device is **bound**
to one or more agents, one of which is its default. A **session** is
one connection episode from one device, wake to close, and a
deployment that has turned recording on stores the session and the
turns inside it. A **conversation** is a
dialogue between a user and exactly one agent: a durable thread that
outlives any single session and belongs to no device. The entity
exists: a stored turn names both the session it was spoken in and the
conversation it belongs to, threads can be listed, read and deleted
over the API and from the command line, and an agent can find one of
its own past threads by description and carry on with it where the
deployment has switched resumption on, with a recap of it if the thread
is too long to pick up whole and they say yes. **Users** arrive in a
later stage, and the model leaves their slot open on purpose, which is
**decided direction** (recorded on this page, 2026-08-21; no owning
issue or decision record yet).

The durable record of all this is one database schema, named `record`.
It holds sessions, threads and the turns both of them project, so SQL
spells the thread table `record.conversations`;
[its reference](reference/conversations-schema.md) is the authority on
what it holds. The reference, the command that prints it and the config
section that switches the store on are all called `conversations`, which
is what a reader is after in it.

## Device

**Implemented today, with the durable record marked inline.**

A device is hardware: buttons, microphone, speaker, display, battery,
an identity (the `Device-Id` it presents), and perhaps a location
("the kitchen"). Under
[thin device, smart server](architecture/guidelines.md#thin-device-smart-server)
it holds no intelligence and no memory; it does not even know agents
exist. What a device contributes to a conversation is *context*, never
memory: its model, its capabilities ("you are speaking through a device
with no display"), its location. Everything learned in conversation
belongs to the agent, so replacing or moving hardware loses nothing.

A device joins a deployment before any of this matters, and that is a
solved problem rather than a planned one: the board is pointed at the
server, the server's OTA endpoint answers its check-in, and an unclaimed
board is claimed through the 6-digit activation ceremony (issue #40,
implemented). The operator's procedure is in
[the server README](../vinga-server/README.md#onboarding-a-device) and
the wire exchange behind it is in
[the Xiaozhi notes](xiaozhi-notes.md#activation-the-6-digit-code-ceremony);
neither is restated here. The word is overloaded, so note which one is
meant: this is a *device* activation, joining a deployment once, and
not the *agent* activation that assembles a prompt at the start of a
session or after a switch.

What the server knows about a device comes from three sources, kept
distinct on purpose:

- **Identity and declaration.** The `Device-Id` on the wire is what
  bindings key on, and the operator's configuration says what was
  *declared* for it: which agents, which default. The
  [configuration reference](reference/domain-config.md) documents the
  fields.
- **Observed facts**: what the device itself reports, arriving in
  phases rather than all at once. The OTA check-in carries the board
  model and the firmware version; the hello carries the protocol
  version and a feature map; a separate background MCP handshake asks
  the device for its own tool list, which a first utterance can beat;
  the first listen message carries the listening mode, which is the
  empirical echo-cancellation signal because the firmware chooses
  realtime exactly when AEC is on; and a `listen` `detect` message
  reports a fired wake word. Every one of those exchanges, the phases
  and the discovery race included, is described on the wire in
  [the Xiaozhi notes](xiaozhi-notes.md#the-device-to-server-protocol).
  How long each fact survives differs by fact, and is a property of the
  server rather than of the domain: what matters here is that none of it lands in a durable,
  queryable per-device record. That record is **decided direction**
  (issue #96).
- **Hardware facts from the board catalog**: what the model implies
  but the wire never says: microphone count, echo cancellation,
  display, button layout. Keyed by the reported board model; the
  per-board [device guides](devices/README.md) are the prose form for
  the help agent, and a machine-readable sibling serves the server.

The help agent reads all three ("this board has one microphone and no
echo cancellation, so I cannot be interrupted mid-reply"). The runtime
adapts to what they imply rather than controlling the device: the
device owns its own listening mode, so adaptation is by observation,
which is the thin-device guideline holding.

## Agent

**Implemented today.**

An agent is what answers: a system prompt, an LLM and the rest of its
provider choices, a voice, an ASR language pin, and the MCP servers
whose tools it may call. The compelling property is focus by
construction: an agent configured with a scoped Home Assistant MCP and
a prompt about one room is an expert on that room and nothing else.
Every field an agent has is in the
[configuration reference](reference/domain-config.md).

The word is chosen deliberately. "Persona" suggests the differences
between agents are cosmetic (a voice, a tone) when the point is that
they differ in capability and scope; it also dresses software as a
human. "Agent" is also what the surrounding ecosystem says, so vinga's
documentation matches what its users already read. In vinga the word
means exactly: a named configuration of prompt, providers, voice, and
tools that holds conversations and accrues memory. Older issues say
"persona"; new writing says agent. The decision and the sweep that
carried it through the server's own text are recorded in
[the 2026-08-12 feature doc](features/2026-08-12-agent-not-persona.md).

## Binding

**Implemented today, with the exception marked inline.**

A binding connects a device to the agents reachable from it, with one
designated default. Bindings are many-to-many: one agent can serve
several devices (the same home agent in every room), and one device can
reach several agents. Today the binding is the device's agent list in
the domain configuration, and the first entry is the default. A device
with no binding reaches the deployment's `default_agent` when one is
set, and is turned away otherwise, so the devices map doubles as an
allowlist.

A fresh wake always gets the default agent: the binding is resolved
when the device connects, so whatever happened in the last session, the
next one starts where the configuration says. Reaching another bound
agent is a [handover](glossary.md#handover). Changing a device's
default by voice ("make Nadia the default agent on this device") is
**decided direction** and belongs to
[the meta capabilities](#meta-capabilities) below.

## Conversation and session

**Implemented today**: the session, the Conversation as a durable,
agent-scoped thread, the stored record of all three, reading and
deleting either entity, resuming a thread by describing it, and the
consented recap of one too long to resume whole (issues #120 and
#190). What issue #190 leaves out of its own scope stays direction and
says so where it appears below.

The load-bearing distinction in the model is that a conversation and a
session are different things.

A **session** is one connection episode: wake (button press or wake
word) to close. It belongs to a device. Sessions exist in the code
today, and so does their record wherever a deployment has turned
recording on: the store then holds one row per session, one per
conversation and one per turn. Whether it records at all, what its
content switches take away when it does, and how long a row is kept
are all in
[the conversation store schema](reference/conversations-schema.md),
which this page does not restate.

A **conversation** is a dialogue between a user and exactly one agent:
a thread that lives on the server, accrues a transcript, and is
independent of any device. The entity exists: a thread takes its
identity when an agent is activated, its row when its first turn is
stored, and its title from the earliest utterance stored on it. The continuity is
built too, behind a switch: with `server.conversations.resumption` on,
an agent describes a past thread out loud, offers what it found, and
carries on with the one the user picks, rebuilding the context from the
stored dialogue under a token budget. Off, an agent's working context
is assembled inside one session and ends when the session closes,
which is exactly the behaviour that predates the switch. The rows
outlast the session either way: they are kept for as long as the
retention policy in
[the store's reference](reference/conversations-schema.md#retention-and-deletion)
says, which is now measured against the thread's own last activity
rather than the session's age.

In short: sessions are how audio reaches the server; conversations are
what accumulates and what you come back to. The vocabulary follows the
same split (issue #190): a session is a connection record, and a
conversation is a thread. "Sophia... let me talk to Nadia... back to
Sophia" is one session touching two threads; resuming with Sophia
tomorrow from another device is the same thread in a new session.

The two are projections of the same rows rather than two stores (issue
#190), and that is implemented. A turn references both the thread it
belongs to and the session it was spoken in, so the session view
(everything said and done on this device from wake to close) and the
conversation view (this thread, across every session it spanned) are
two readings of one set of turns. No dialogue is written twice, and
neither view is reconstructed from the other.

Two things sit beside that record rather than inside it. Capture and
the structured event stream are scoped to a session, never to a thread,
and that is implemented today. And a turn that is only a meta request
("increase the volume to 9") is session work rather than dialogue with
an agent, so it belongs to the session and to no thread; the honest
edge is that a mixed turn ("set the volume to 9, and what were we
saying?") belongs to the thread. That recording rule is **decided
direction** (recorded on this page, 2026-08-21; no owning issue or
decision record yet).

The split is what makes the desired behaviors ordinary instead of
special cases:

- **Switching and returning.** "Let me talk to Nadia" leaves the
  current thread and opens or resumes one with Nadia inside the same
  session; "back to Sophia" returns. The switch exists today as the
  handover tool, and so do the threads: the first activation of an
  agent in a session opens one and every later activation continues it,
  so the record of that session names two of them: the handover turn
  belongs to the thread it started on, and the greeting the incoming
  agent answers with is the first turn of its own thread. The incoming
  agent starts clean, which the last bullet below states.
- **Resuming elsewhere.** *Implemented today, issue #190.* A new
  session on another device attaches to an existing thread. Discovery
  is by spoken description ("a while ago we were talking about this
  topic") and is agent-scoped: an agent finds its own past threads and
  no other agent's, and it can only pick up one it has just offered.
- **Cost.** "How much has this conversation cost so far" wants cost to
  be a property of the thread. It is **decided direction** (recorded on
  this page, 2026-08-21; no owning issue or decision record yet):
  issue #190 explicitly leaves budgets and per-conversation accounting
  out of its scope, so nothing owns this yet.

The decided semantics, each with its owner:

- **A switch lasts for the session.** *Implemented today.* The next
  wake of the device gets its default agent again, because the binding
  is resolved at connect and carries no memory of the last session, so
  the wake experience stays predictable.
- **A new activation starts a fresh thread, and resumption is always
  explicit.** *Implemented today, issue #190.* Waking a device does not
  silently drop the user back into whatever was being discussed
  yesterday; continuing an earlier thread is asked for, by describing
  it. This replaces an earlier formulation on this page under which
  conversations were suspended and never ended.
- **Retention knows about threads.** *Implemented today, issue #190.*
  The window is measured against a conversation's last activity rather
  than a session's age, so a thread that is still being talked to keeps
  its turns however old the session that began it, and a thread past
  the window goes whole. Exactly what the three rules do is in
  [the store's reference](reference/conversations-schema.md#retention-and-deletion).
- **Threads are listable, readable and deletable.** *Implemented
  today, issue #190.* An operator lists an agent's threads, reads one
  with its dialogue, and deletes one, over `/api/conversations` or with
  `vinga conversation list|show|delete` in front of it. Deleting a
  thread takes its turns out of whatever sessions they were spoken in
  and leaves those sessions standing with a gap, which is the opposite
  direction from deleting a session; neither ever comes back.
- **A long thread gets a recap only by consent.** *Implemented today,
  issue #190.* When a thread is longer than the agent can be given at
  once, it offers a choice rather than silently compressing: a short
  recap of the whole of it, or carrying on from the recent part. If the
  user says yes, the agent speaks the recap itself and only then is it
  kept, as a checkpoint the conversation is rebuilt from afterwards; a
  recap the user did not hear to the end is never stored, and the next
  resume offers the same choice again. Declining stores nothing. This
  replaces an earlier formulation on this page, which warned about
  length and offered to summarize and start fresh from the summary.
- **Resumption is a deployment switch, and it needs the text.**
  *Implemented today, issue #190.* A thread cannot be resumed from rows
  that were never written, so resumption is available only where
  conversation text is stored, which is one of the two switches
  [the store's reference](reference/conversations-schema.md) describes.
  A configuration that asks for resumption with recording or text off
  is refused at boot, in a sentence naming both keys.
- **A switch starts clean by default.** *Implemented today, issue
  #190*, as the fresh-thread default applied to a handover. The
  incoming agent does not read what was said to the outgoing one.
  Agents are scoped on purpose, and a switch that silently handed the
  whole session to the incoming agent would leak around that scoping;
  it would also move words spoken to a local agent to whatever provider
  the incoming agent uses. What the incoming agent starts with is a
  fixed instruction to greet and carry on, and switching back returns
  the agent to its own thread with what it said on it. Carrying context
  deliberately (phrasing that asks for continuation, "ask Nadia about
  this", with the agent asking rather than guessing when the phrasing
  is ambiguous) is the part that remains **decided direction**.

## Configuration changes arrive as whole worlds

**Implemented today.**

Editing the domain configuration (an agent, its prompt, a provider
entry, an MCP server) neither restarts the server nor mutates it in
place. The server serves immutable states called
[worlds](glossary.md#world): a validated configuration, the stored
secrets opened behind it, and everything built from the pair, frozen
together. Applying stored changes composes and builds the complete next
world first, so a refused apply has changed nothing, and then swaps it
in at one point. What still waits for a restart is the server's own
file (ports, auth), which holds nothing the configuration API writes.

The user-visible semantic is what happens when a live conversation
meets a change, and the answer is: it arrives at the conversation's own
natural boundaries, never mid-turn. A conversation already speaking
finishes on the world it was built from, served that world's prompt to
the end, even if its agent was deleted from the store mid-sentence.

Which boundary a particular edit waits for depends on what moved, and
that is a mechanic rather than a semantic: the
[configuration reference](reference/domain-config.md) says it kind by
kind, and [the glossary's world entry](glossary.md#world) states the
rule in one paragraph.

## The wake word wakes the device, not an agent

**Implemented today, with one open question marked inline.**

This is settled by hardware reality, and the documentation should say
it plainly wherever wake words appear. The wake word is spotted on-chip
and the server takes no part in the decision: it cannot hear, tune, or
substitute for it, and what it is told is which word fired, after the
fact.
[The Xiaozhi notes](xiaozhi-notes.md#the-wake-word-is-spotted-on-the-chip-and-the-server-takes-no-part-in-it)
describe the detection, the report that carries it, and the firmware
option that decides whether the buffered trigger audio is sent along
with it; whether the prebuilt
images on our boards send that audio has not been checked on the wire
and is open (issue #112).

Wake words are also a fixed compiled set, so per-agent wake words are
impossible on stock firmware, which is
[the compatibility floor](architecture/product-promises.md#stock-xiaozhi-firmware-is-the-compatibility-floor).

So the wake word is the doorbell: it opens a session, and the device's
default agent answers. When a board's wake word happens to be "Sophia"
and its default agent is Sophia, that is a pleasing illusion produced
by configuration, not a mechanism, and it breaks the moment a second
agent is bound to the device. The help agent knows whether its device
has a wake word enabled and explains exactly this.

## Memory

**Implemented today, with the direction marked inline.**

Memory has three scopes, and what tells them apart is whose the
remembered thing is:

- **Agent scope** is what an agent knows about the user, keyed by the
  agent and never by the device, because an agent is one entity across
  rooms: "remember I am vegetarian", said in the kitchen, holds in the
  bedroom. This is the memory that has always existed, and the
  `remember` tool writes it.
- **Conversation state** is a keyed ledger of what is currently true in
  the conversation happening now, written with `set_state` and cleared
  with `clear_state`. It is not a record of what was said; it is what
  the assistant would lose track of otherwise, and each name holds one
  current value that writing the same name again replaces.
- **Device scope** is what is known about the place and its household,
  shared by every agent bound to that device. Its tools arrive with the
  editing family.

All three are present in every deployment: there is nothing to
configure and nothing to switch on. They are stored in Postgres, in a
schema of the server's own that it migrates at every boot (issue #314);
the `memory:` section that used to name a directory of files has
retired with the files.

The injected prompt states its own precedence, because the assistant is
the one reader that cannot see where a line came from: the conversation
first, then the agent's remembered facts, then the device's notes, each
under a heading saying which of the three it is and which of them wins.
What is most current wins.

That injection is also where memory leaves the host, and the two halves
of the answer are worth stating separately. As **storage**, memory never
leaves: it is rows in the deployment's own database and no other server
is told about them. As **prompt content**, it goes wherever the rest of
the prompt goes: what a conversation is keeping and what the agent
remembers are read into every reply, so they follow the active LLM
provider's egress exactly as the transcript and the persona do. An agent
on a cloud model sends what it remembered along with what was just said.
`server.local_only` is the existing guard and it is the same one: a
provider that sends session data off the host cannot be booted under it.

**Conversation state shares its conversation's lifetime, exactly.** It
is keyed by the thread rather than by the connection, so it survives a
device hanging up and comes back when that conversation is resumed; and
it is deleted in the same transaction as the thread, whether the thread
goes because somebody erased it or because retention pruned it. The
consequence is worth stating plainly: a deployment that does not store
conversation text cannot resume a thread at all, so every conversation
there starts with an empty ledger. Anything that should outlive the
conversation has to be promoted to agent memory with `remember`, which
is what a game agent's "save the game" and a tutor's "you have mastered
this" actually are.

Whether a *particular* agent may remember at all is not answerable
today, deliberately: per-agent control arrives with the scopes' own
issue, and until then every agent is offered the memory tools.

One decided direction builds on the keying:

- **When users arrive the key becomes the (user, agent) pair**, so an
  agent shared by a household remembers each person separately. This
  refinement is **decided direction** (recorded on this page,
  2026-08-21; no owning issue or decision record yet): issue #83 covers
  neither a user-bearing key nor the profile below.

One deliberate hole in agent isolation is planned: a small shared
**user profile** (name, language, standing preferences) visible to all
of a user's agents, so nobody teaches five agents their name five
times. It is a hole on purpose, and it is documented as one: agents
stay isolated in what they learn, except for the profile the user chose
to share with all of them. This is **decided direction** (recorded on
this page, 2026-08-21; no owning issue or decision record yet).

Agent memory is distinct from what an agent appears to know inside one
conversation, and the three are easy to conflate. What the assistant
can see of the conversation it is in is its dialogue: it is reading the
transcript, not remembering anything. What it writes down with
`set_state` is the second kind, and it goes when that conversation
does. Only what it remembers with `remember` is the kind that is still
there next month.

## Meta capabilities

**Decided direction** (recorded on this page, 2026-08-21; no owning
issue or decision record yet), except where a claim below cites its
own owner.

Some questions must be answerable in every conversation, whoever is
answering: "how much has this conversation cost", "find the
conversation where we discussed the trip and resume it here", "let me
talk to Nadia". These are not features of any one agent; they are vinga
capabilities, modeled as a small set of built-in tools injected into
every agent's tool set, exactly parallel to how the device's own
controls already reach agents as MCP tools. Three of them exist today:
the handover tool, and the two that move a session between threads
(start a new conversation, find and resume an earlier one). All three
execute in vinga-owned code and log their reason, per
[the decision-reason guideline](architecture/guidelines.md#give-every-decision-a-reason-and-know-whose-reason-it-is).

Scoping decision: **conversation search is agent-scoped** (issue #190).
An agent can find and resume its own past threads, not another agent's.
That preserves the focus story and the credential scoping that make
per-agent MCP configuration worth having; it is a privacy boundary, not
a convenience default. A cross-agent search may arrive later as a
separate, explicitly user-level capability.

The cost question ("how much has this conversation cost") and the
recording rule for meta turns are both stated in
[Conversation and session](#conversation-and-session) above, and both
are unowned there for the same reason: issue #190 leaves budgets,
per-conversation accounting and cross-agent threads out of its scope.

## The help agent

**Decided direction** (issue #21), except the device guides, which
exist.

A built-in agent, bound to every device by default, that answers three
kinds of question:

- **This device**: which button starts a conversation, how long to hold
  it to power off, what the display shows. Its source is the per-board
  device guide, selected by the device model at runtime, so it explains
  the hardware actually in front of the user.
- **This system**: vinga's concepts, the contents of this page: what an
  agent is, what a conversation is, why the wake word wakes the device
  and not an agent.
- **Device commands**: the controls the device itself publishes as MCP
  tools (volume, screen brightness), phrased as things the user can
  just say.

There is one help agent, not one per board (issue #21). Its prompt is
composed at the start of a session from a shared part plus a block of
facts for the board that checked in, keyed on the reported board model,
and a board the deployment has no facts for gets an honest vague
answer rather than a confident wrong one.

The [device guides](devices/README.md) that feed it are user-facing
markdown, one per supported board, and they exist today (issue #93), so
the help agent's knowledge is reviewable documentation rather than
prompt text.

## Before users arrive

**Implemented today** as a limitation, with the direction marked
inline.

There is no user entity. "The user" is implicitly whoever is talking to
the device, and memory is effectively keyed by (device owner, agent). A
household sharing one device is one "user" to every agent on it, and
enabling conversation-text storage on a shared device therefore stores
what guests say to it, which is the same statement
[the store's reference](reference/conversations-schema.md) makes.

Users, and with them budgets and voiceprint identification for shared
devices, come in a later stage: when they arrive, conversations, memory
and the shared profile all gain a user in their key, and voiceprint
recognition decides which user is speaking on a shared device. That is
**decided direction** (recorded on this page, 2026-08-21; no owning
issue or decision record yet). It is stated here so the later refactor
has a name rather than being a surprise.
