# Domain concepts

**Date:** 2026-08-11

The domain model of samtal from the user's point of view: the nouns,
how they relate, and the semantics that were decided on purpose. The
[glossary](glossary.md) defines each term in one paragraph for looking
things up; this page explains how the terms fit together and why. The
model is deliberately ahead of the code: some of it is implemented
today, some is decided direction, and each section says which. The
[principles page](architecture/principles.md) holds the promises this
model must keep; where the two touch, the promise is cited.

## The model in one paragraph

A **device** is a physical endpoint with no intelligence of its own.
An **agent** is a named unit of behavior: a system prompt, a model
configuration, a voice, and a set of MCP tools. A device is **bound**
to one or more agents, one of which is its default. A **conversation**
is a dialogue between a user and exactly one agent; it lives on the
server, independent of any device. A **session** is one connection
episode from one device, wake to close; a session attaches to a
conversation, it is not the conversation. **Users** arrive in a later
stage, and the model leaves their slot open on purpose.

## Device

A device is hardware: buttons, microphone, speaker, display, battery,
an identity (the `Device-Id` it presents), and perhaps a location
("the kitchen"). Under the thin-device promise it holds no
intelligence and no memory; it does not even know agents exist. What a
device contributes to a conversation is *context*, never memory: its
model, its capabilities ("you are speaking through a device with no
display"), its location. Everything learned in conversation belongs to
the agent, so replacing or moving hardware loses nothing.

What the server knows about a device comes from three sources, kept
distinct on purpose:

- **Identity and declaration.** The `Device-Id` on the wire is what
  bindings key on, and the operator's configuration says what was
  *declared* for it: which agents, which default.
- **Observed facts**: what the device itself reports. Board model and
  firmware version arrive with the OTA request; protocol version, a
  feature map, and the device's own MCP tool list arrive at hello;
  the listening mode arrives with the first listen message and is the
  empirical echo-cancellation signal, since the firmware chooses
  realtime exactly when AEC is on; a fired wake word is reported by
  word. Today these are parsed and dropped; keeping them per device
  is planned (issue #96).
- **Hardware facts from the board catalog**: what the model implies
  but the wire never says: microphone count, echo cancellation,
  display, button layout. Keyed by the reported board model; the
  per-board device guides are the prose form for the help agent, and
  a machine-readable sibling serves the server.

The help agent reads all three ("this board has one microphone and no
echo cancellation, so I cannot be interrupted mid-reply"). The
runtime adapts to what they imply rather than controlling the device:
the device owns its own listening mode, so adaptation is by
observation, which is the thin-device promise holding.

## Agent

An agent is what answers: a system prompt, an LLM and the rest of its
provider choices, a voice, an ASR language pin, and the MCP servers
whose tools it may call. The compelling property is focus by
construction: an agent configured with a scoped Home Assistant MCP and
a prompt about one room is an expert on that room and nothing else.

The word is chosen deliberately. "Persona" suggests the differences
between agents are cosmetic (a voice, a tone) when the point is that
they differ in capability and scope; it also dresses software as a
human. "Agent" is also what the surrounding ecosystem says, so
samtal's documentation matches what its users already read. In samtal
the word means exactly: a named configuration of prompt, providers,
voice, and tools that holds conversations and accrues memory. Older
issues say "persona"; new writing says agent.

## Binding

A binding connects a device to the agents reachable from it, with one
designated default. Bindings are many-to-many: one agent can serve
several devices (the same home agent in every room), and one device
can reach several agents. Today the binding is the device's agent list
in the domain configuration, and the first entry is the default. A
device with no binding reaches the deployment's `default_agent` when
one is set, and is turned away otherwise, so the devices map doubles
as an allowlist. A fresh wake always gets the default agent; reaching
another bound agent is a [handover](glossary.md#handover), and a
planned meta capability lets the user change a device's default by
voice ("make Nadia the default agent on this device").

## Conversation and session

The load-bearing distinction in the model is that a conversation and a
session are different things.

A **session** is one connection episode: wake (button press or wake
word) to close. It belongs to a device. Sessions exist in the code
today.

A **conversation** is a dialogue between a user and exactly one agent.
It lives on the server, accrues a transcript and a cost, and is
independent of any device. This is decided direction, not yet code:
today conversation history lives only as long as the session that
produced it.

In short: sessions are how audio reaches the server; conversations
are what accumulates and what you come back to. "Sophia... let me
talk to Nadia... back to Sophia" is one session touching two
conversations; resuming with Sophia tomorrow from another device is
the same conversation in a new session. The model keeps the link in
both directions: a session records, in order, the conversations it
touched, and a conversation records the sessions it was part of,
beginning with the one that opened it. Meta capabilities read that
linkage ("what did we talk about this morning on the kitchen
device").

A consequence of the decisions below, stated so nothing rediscovers
it: the **session transcript** is its own artifact, distinct from any
conversation's transcript. A session's full record is what was said
and done on the device from wake to close: the entries of every
conversation it touched, in order, plus the meta turns that belong to
no conversation. It is reconstructed from the session's ordered
conversation references and its session events, so no dialogue is
stored twice. This is also the record device-side diagnostics
already live in: capture and the structured event stream are scoped
to a session, not to a conversation.

The split is what makes the desired behaviors ordinary instead of
special cases:

- **Switching and returning.** "Let me talk to Nadia" suspends the
  current conversation and opens (or resumes) one with Nadia inside
  the same session; "back to Sophia" resumes the suspended one.
- **Resuming elsewhere.** A new session on another device attaches to
  an existing conversation; "a while ago we were talking about this
  topic" is a search over past conversations followed by an attach.
- **Cost.** "How much has this conversation cost so far" is answerable
  because cost is a property of the conversation entity.

Three decided semantics:

- **A switch lasts for the session.** The next wake of the device gets
  its default agent again, so the wake experience stays predictable.
- **Conversations are suspended, never ended.** There is no "end
  conversation" in the model. The consequences are features to build:
  cleanup of old conversations, a warning when one grows very long,
  and an offer to summarize it and start fresh from the summary.
- **A switch starts clean by default.** The incoming agent does not
  read what was said to the outgoing one. Agents are scoped on
  purpose, and a switch that silently handed the whole session to the
  incoming agent would leak around that scoping; it would also move
  words spoken to a local agent to whatever provider the incoming
  agent uses. Carrying context is explicit: phrasing that asks for
  continuation ("ask Nadia about this") carries it, and when the
  phrasing is ambiguous the agent asks rather than guessing. What is
  deliberately carried becomes part of the new conversation. Nothing
  is lost by starting clean, since the suspended conversation is
  still there to come back to. Today's handover behaves differently
  (the session transcript carries across); it adopts this rule when
  conversations become persistent entities.

## The wake word wakes the device, not an agent

This is settled by hardware reality, and the documentation should say
it plainly wherever wake words appear. The wake word is spotted
on-chip by ESP-SR; the audio never reaches the server, which at most
is told which word fired, after the fact (the firmware's `listen`
`detect` report; [xiaozhi-notes](xiaozhi-notes.md)). Wake words are
also a fixed compiled set, so per-agent wake words are impossible on
stock firmware, which is the compatibility floor.

So the wake word is the doorbell: it opens a session, and the device's
default agent answers. When a board's wake word happens to be "Sophia"
and its default agent is Sophia, that is a pleasing illusion produced
by configuration, not a mechanism, and it breaks the moment a second
agent is bound to the device. The help agent knows whether its device
has a wake word enabled and explains exactly this.

## Memory

Memory is keyed by agent, never by device, because an agent is one
entity across rooms. This is already the implemented behavior (one
memory file per agent) and it survives into the target model with one
refinement: when users arrive, the key becomes the (user, agent) pair,
so an agent shared by a household remembers each person separately.

One deliberate hole in agent isolation is planned: a small shared
**user profile** (name, language, standing preferences) visible to all
of a user's agents, so nobody teaches five agents their name five
times. It is a hole on purpose, and it is documented as one: agents
stay isolated in what they learn, except for the profile the user
chose to share with all of them.

Agent memory is distinct from what an agent appears to know inside one
conversation; the config reference documents that distinction where
memory is configured.

## Meta capabilities

Some questions must be answerable in every conversation, whoever is
answering: "how much has this conversation cost", "find the
conversation where we discussed the trip and resume it here", "let me
talk to Nadia". These are not features of any one agent; they are
samtal capabilities, modeled as a small set of built-in tools injected
into every agent's tool set, exactly parallel to how the device's own
controls already reach agents as MCP tools. The switch itself executes
in samtal-owned code and logs its reason, per the decision-sites
principle.

Scoping decision: **conversation search is agent-scoped.** An agent
can find and resume its own past conversations, not another agent's.
That preserves the focus story and the credential scoping that make
per-agent MCP configuration worth having; it is a privacy boundary,
not a convenience default. A cross-agent search may arrive later as a
separate, explicitly user-level capability.

Recording decision: **meta turns stay out of the conversation.**
"Increase the volume to 9" is work for the session, not part of the
dialogue with the agent, so a turn that is only a meta request
(device control, a cost question, the switch itself) is recorded as a
session event rather than an entry in the conversation transcript.
Resuming a conversation months later replays the dialogue, not the
volume adjustments. The honest edge: a mixed turn ("set the volume to
9, and what were we saying?") belongs to the conversation.

## The help agent

A built-in agent, bound to every device by default, that answers three
kinds of question:

- **This device**: which button starts a conversation, how long to
  hold it to power off, what the display shows. Its source is the
  per-board device guide, selected by the device model at runtime, so
  it explains the hardware actually in front of the user.
- **This system**: samtal's concepts, the contents of this page: what
  an agent is, what a conversation is, why the wake word wakes the
  device and not an agent.
- **Device commands**: the controls the device itself publishes as
  MCP tools (volume, screen brightness), phrased as things the user
  can just say.

The device guides that feed it are user-facing markdown, one per
supported board, linked from the hardware tables (issue #93), so the
help agent's knowledge is reviewable documentation rather than prompt
text.

## Before users arrive

Users, and with them budgets and voiceprint identification for shared
devices, come in a later stage. Until then "the user" is implicitly
whoever is talking to the device, and memory is effectively keyed by
(device owner, agent). A household sharing one device is one
"user" to every agent on it. This is a documented limitation, stated
here so the later refactor has a name, not a surprise: when users
arrive, conversations, memory, and the shared profile all gain a user
in their key, and voiceprint recognition decides which user is
speaking on a shared device.
