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
in configuration, and the first entry is the default. A fresh wake
always gets the default agent; reaching another bound agent is a
[handover](glossary.md#handover), and a planned meta capability lets
the user change a device's default by voice ("make Nadia the default
agent on this device").

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

Two decided semantics, and one edge left open:

- **A switch lasts for the session.** The next wake of the device gets
  its default agent again, so the wake experience stays predictable.
- **Conversations are suspended, never ended.** There is no "end
  conversation" in the model. The consequences are features to build:
  cleanup of old conversations, a warning when one grows very long,
  and an offer to summarize it and start fresh from the summary.
- **Open: what a handover shows the incoming agent.** Today the
  session transcript carries across a handover, so the incoming agent
  reads everything said since wake. Once conversations are persistent
  per-agent entities, what the incoming agent may read (the whole
  session so far, a summary, nothing) becomes a real choice with a
  privacy component, and it is not made here.

## The wake word wakes the device, not an agent

This is settled by hardware reality, and the documentation should say
it plainly wherever wake words appear. The wake word is spotted
on-chip by ESP-SR and never reaches the server; the server learns only
that a session opened ([xiaozhi-notes](xiaozhi-notes.md)). Wake words
are also a fixed compiled set, so per-agent wake words are impossible
on stock firmware, which is the compatibility floor.

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
