"""The shapes the configuration API answers and accepts.

Pydantic models, in a module that imports pydantic and nothing else,
which is the whole reason they are not in `api.py`. Two surfaces know
these shapes and only one of them may pay for FastAPI: the API declares
them as its response models, and the CLI renders an answer it received
over HTTP by validating it against the shape the API said it would send
rather than against a hand-kept list of the keys it expects. The CLI
must not import the API for it: `config schema` and `config reference`
would then pay for FastAPI on their way to printing a document that has
nothing to do with it.

"Nothing else" is why one description below writes the mask out as a
literal rather than reading `secrets.MASK`: this module sits under
`entities`, which sits under `loader`, which is what `secrets` imports,
so the import that would derive it is a cycle. The two are held equal
from the outside instead, by the pin in `test_api_openapi.py` that looks
for the constant in the rendered document, which is the byte a client
reads and the one that must not drift.

Beside the models, and for the same reason, the three runtime surfaces
the API is handed: the protocol a status read is taken through, and the
shapes of the callables the generalized reload is applied by and the
stored-versus-running comparison is read through. All three are stated
in typing and these models, which is what lets a route say what it was
handed without the module that renders the document loading the MCP SDK
to find out.

Every model forbids extra keys, so a field that is answered is a field
that was declared. The descriptions are the document's prose, written
for whoever reads the contract rather than the code, and they are
committed bytes: `docs/reference/api-openapi.json` carries them, and
the drift check compares them.
"""

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

# The transport shapes
#
# Declared as response models so that the document carries real schemas
# rather than the empty objects an untyped dictionary return would
# produce. They are shapes and not a second validation layer: what
# `views` builds passes through them unchanged, and nothing here decides
# what a read may show.


class SecretSlot(BaseModel):
    """One slot of an entity that holds a secret stored in the database."""

    model_config = ConfigDict(extra="forbid")

    # Nullable and required, not optional: every read answers with the
    # key or with null, and a client that has to tell "no reference" from
    # "the server did not say" has been given a third state it cannot
    # act on.
    shadows: str | None = Field(
        description=(
            "The entity key this stored secret displaces, or null when the entity "
            "writes no reference for the slot. A stored secret takes precedence over "
            "an environment reference written for the same slot, and this names what "
            "it takes the place of."
        ),
    )


class Envelope(BaseModel):
    """One entity as a read returns it: the entity, and its stored-secret
    slots beside it."""

    model_config = ConfigDict(extra="forbid")

    entity: dict[str, Any] = Field(
        description=(
            "The entity's body, with every secret-bearing value masked, in the shape "
            "a write of it accepts and resubmittable as it stands. A PUT of it "
            "replaces the model-shaped half and never the credentials stored beside "
            "it; a field this read leaves out is one whose absence is what it means, "
            "and it means the same absence on the way back. An environment reference "
            # The mask, written out: see the module docstring for why it
            # is not read from `secrets.MASK` here, and where the two are
            # held equal.
            "reads back as itself; a value shown as the mask, `********`, resubmits "
            "as keep the stored value, which is substituted before the fragment is "
            "validated, and that mask written where nothing is stored is refused. "
            "Described "
            "rather than validated here, because the mask is not a value the entity "
            "model would accept on its own: the entity schemas under "
            "`components/schemas` are what say which keys a write may carry."
        )
    )
    secrets: dict[str, SecretSlot] = Field(
        description=(
            "The slots holding a secret stored in the database, by slot name, and "
            "never their values: reads are masked. Empty for the kinds that can hold "
            "no stored secret (prompt fragments, agents, agent defaults, devices), so "
            "that every read has one shape. Display-only, and nothing to act on when "
            "resubmitting the entity above: a stored secret is left exactly as it is "
            "by a write of the entity, and rotating one is the secret PUT, which is "
            "the one door a plaintext value enters by."
        )
    )


class StoredSecretLocation(BaseModel):
    """Where one stored secret is, in the whole-configuration read."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description="The kind of entity holding it: provider or mcp_server.")
    identity: str = Field(
        description=(
            "The entity's identity: `<stage>.<name>` for a provider, the name for an "
            "MCP server."
        )
    )
    slot: str = Field(description="The credential slot inside that entity.")
    shadows: str | None = Field(
        description="The entity key this stored secret displaces, or null."
    )


class ConfigDocument(BaseModel):
    """The whole domain configuration of one deployment, masked."""

    model_config = ConfigDict(extra="forbid")

    config: dict[str, Any] = Field(
        description=(
            "The domain half of the configuration (providers, MCP servers, agent "
            "defaults, agents, devices, the default agent) in the shape "
            "`docs/reference/domain-config.md` documents, with every secret-bearing "
            "value masked."
        )
    )
    secrets: list[StoredSecretLocation] = Field(
        description=(
            "Where every secret stored in the database is, which the masked document "
            "above cannot say. A list rather than a mapping, because a location is "
            "three fields and not a key."
        )
    )


class PendingDevice(BaseModel):
    """One device waiting to be claimed, as the listing shows it.

    The listing is keyed by the code, because the code is what the
    operator has: they are holding a board with six digits on it, and
    the question the board model and the firmware version answer is
    which of these entries is that board.
    """

    model_config = ConfigDict(extra="forbid")

    mac: str = Field(
        description=(
            "The device's MAC in canonical form, which is the row a successful claim "
            "writes."
        )
    )
    client_id: str = Field(
        description="The UUID the device sent as its Client-Id at its last check-in."
    )
    board: str = Field(
        description=(
            "The board type the device reported, such as "
            "waveshare-esp32-s3-touch-lcd-1.54, or `unknown` when it reported none. "
            "Whatever the device said, bounded in length and stripped of anything "
            "unprintable."
        )
    )
    firmware: str = Field(
        description=(
            "The firmware version the device reported, or 0.0.0 when it reported none."
        )
    )
    first_seen: str = Field(
        description="When this device first checked in, as an ISO-8601 instant in UTC."
    )
    last_seen: str = Field(description="Its most recent check-in, in the same form.")
    expires_at: str = Field(
        description=(
            "When this code stops being claimable. The device re-checks every couple of "
            "minutes and displays whatever the fresh reply carries, so an expired code "
            "is replaced on the screen rather than leaving the device stranded."
        )
    )


class McpServerStatus(BaseModel):
    """One configured MCP server, as the running server sees it.

    The listing is keyed by the entry's name, because that is what the
    operator wrote and what every tool the server publishes is prefixed
    with.
    """

    model_config = ConfigDict(extra="forbid")

    state: Literal["connected", "down", "unused"] = Field(
        description=(
            "What this entry is doing: `connected` and offering the tools below, "
            "`down` and offering none, or `unused` because no agent references it, so "
            "this server never built a connection for it at all."
        )
    )
    reason: str | None = Field(
        description=(
            "Why a `down` server is down, as a fixed token this server owns: the class "
            "of the failure, or `DroppedAfterFailedCall` for a connection dropped after "
            "a tool call failed on it. Null when it is not down. Never a message the "
            "far side wrote, since an MCP server is a third party and its bytes are not "
            "this API's to publish."
        )
    )
    since: str = Field(
        description=(
            "When this state was last entered, as an ISO-8601 instant in UTC. A new "
            "reason for staying down counts as entering it again, since it is a fresh "
            "failure. For an entry no agent references it is when the running "
            "configuration took effect."
        )
    )
    tools: list[str] = Field(
        description=(
            "What this server published, under the names the model is given "
            "(`<entry>__<tool>`, sanitized). Empty while it is down. Only names cross "
            "this surface: a description, or the name a server listed before the "
            "publishing rule got to it, is bytes that server chose, and a server "
            "holding a credential of this deployment's could reflect it in either."
        )
    )
    grants: dict[str, list[str] | None] = Field(
        description=(
            "Which agents may reach this server, by agent name. The value is how much "
            "of the server the agent gets: null is the whole server, and a list is the "
            "tools that agent was allowed, by the published name without the entry "
            "prefix. Beside the published list above it, so an allowed name this "
            "server does not offer is answerable in one read."
        )
    )


class McpReloadResult(BaseModel):
    """What one reload did, and what is running once it had done it.

    Both halves in one answer on purpose: the request that applies a
    change is the request that says what the change was and how it came
    out, so believing a write took effect when it did not takes a
    deliberate act of not reading the reply.
    """

    model_config = ConfigDict(extra="forbid")

    started: list[str] = Field(
        description=(
            "The entries that had no connection before this reload and have one now: "
            "newly written, or newly named by some agent's `mcp` list. Started is not "
            "connected: an entry here whose server was unreachable is `down` below, "
            "with its reason."
        )
    )
    restarted: list[str] = Field(
        description=(
            "The entries whose fragment or whose stored secrets changed. Their "
            "connections were closed and made again, so a rotated credential applies "
            "as this request answers rather than at some later boundary."
        )
    )
    stopped: list[str] = Field(
        description=(
            "The entries this server no longer connects: deleted, or no longer named "
            "by any agent. A deleted one is gone from the status below; a "
            "de-referenced one is still there, as `unused`."
        )
    )
    unchanged: list[str] = Field(
        description=(
            "The entries that kept the connection they had. It is a statement about "
            "the connection and not about the entry's text: an entry whose "
            "`instructions` was rewritten is here, because that field configures a "
            "prompt rather than a connection, and restarting a live connection to "
            "apply it would drop mid-call tools and respawn a stdio child for nothing. "
            "The conversations using such an entry keep the tools they had and the "
            "guidance they were activated with; the new guidance reaches them at their "
            "next activation."
        )
    )
    servers: dict[str, McpServerStatus] = Field(
        description=(
            "What every configured entry is doing now that the reload has been applied, "
            "keyed by entry name: exactly what `GET /runtime/mcp-servers` answers, "
            "taken in the same breath so that applying and verifying are one round "
            "trip."
        )
    )


class McpStatusSource(Protocol):
    """Where the two answers above come from: the running server's MCP
    registry, through the one method this API asks it for.

    A protocol and not the class, declared here beside the shapes it
    answers in, because the registry imports the MCP SDK and this
    project's provider layer and rendering the committed document must
    load neither: `api.py` records that constraint and used to pay for
    it with `Annotated[Any]` dependencies, which is a route saying it
    knows nothing about what it was handed. This says the true and much
    smaller thing instead, in typing and pydantic and nothing else.

    An application built without a server around it has no registry and
    is handed None, which the status read answers as an empty object.
    """

    def typed_status(self) -> dict[str, McpServerStatus]: ...


# What one reload applied, kind by kind
#
# The whole schema is published at once, sections this server does not
# fill yet included, and that is the #193 lesson rather than a taste:
# every model here forbids extra keys, so a client generated from a
# smaller schema would reject a grown answer. A section whose milestone
# has not landed answers null, and the milestone that lands it changes
# a value and a description and never the shape (#191).


class PromptsReload(BaseModel):
    """The prompt text a reload put in front of the agents that use it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    changed: list[str] = Field(
        description=(
            "The agents whose own prompt or whose resolved shared fragments now differ "
            "from what this server was serving, sorted. Each of them assembles the new "
            "text at its next activation, which is a new session or an agent switch; a "
            "conversation already in progress keeps the prompt it was activated with. "
            "The MCP guidance that also goes into an assembly is not counted here: what "
            "moved there is reported entry by entry under `mcp`, since that is where a "
            "connection is what changed. An agent this reload added or removed is not "
            "here either: it is named under `agents`, since an agent that has just "
            "arrived has no previous text to differ from and one that has just gone "
            "will not assemble again."
        )
    )


class FillersReload(BaseModel):
    """What a reload did to the pre-synthesized filled pauses.

    Three outcomes and no fourth, because a reload makes exactly one of
    three decisions about an agent that masks its latency. An agent that
    masks none, or has just switched masking off, is in none of the
    three: there was no decision to make about it, and naming it under
    an outcome would say there had been.

    Which clips a conversation plays is decided when it opens, so
    everything here reaches the next conversation and none of it changes
    what an open one is masking with.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    resynthesized: list[str] = Field(
        description=(
            "The agents whose filler clip was made again, sorted: a field of the "
            "effective `filler` section moved, or the voice that speaks it did, which "
            "is now a thing a reload can move as well. The "
            "whole section is the unit, so an edit to `delay_ms` alone is here too, "
            "with audio identical to what it replaced; every agent named here cost a "
            "round of text-to-speech work at the configured provider."
        )
    )
    reused: list[str] = Field(
        description=(
            "The agents whose filler clip was carried over unchanged, sorted. Reuse is "
            "the point of the comparison: an edit to a prompt never re-synthesizes a "
            "clip, and neither does an edit to a provider entry no masked agent speaks "
            "through. Rewriting the entry one does speak through is the voice moving, "
            "and puts that agent under `resynthesized` instead."
        )
    )
    disabled: list[str] = Field(
        description=(
            "The agents whose synthesis failed, sorted. The reload applied and those "
            "agents run with the latency mask off, because a filler is a mask and a "
            "posture where a text-to-speech hiccup blocked a prompt fix would invert "
            "what matters. The next reload tries again."
        )
    )


class ProvidersReload(BaseModel):
    """What a reload did to the built providers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    built: list[str] = Field(
        description=(
            "The provider entries this reload constructed, as `<stage>.<name>`, sorted: "
            "written since, or changed in a way that a running instance cannot be."
        )
    )
    reused: list[str] = Field(
        description=(
            "The provider entries carried into the new world as the objects they "
            "already were, sorted. An entry whose model and stored credentials are "
            "unchanged is never built again, which is what keeps a prompt edit from "
            "reloading a local model."
        )
    )
    retired: list[str] = Field(
        description=(
            "The provider entries no world after this one uses, sorted. Their resources "
            "are released once the last conversation holding them has ended, not at the "
            "instant this answered."
        )
    )


class AgentsReload(BaseModel):
    """What a reload did to the set of agents this server can serve.

    The set moves at the swap and nowhere else: an agent named under
    `added` is servable from the instant this answered, and one under
    `removed` cannot be reached by anything that starts after it, while
    every conversation already talking as it finishes on the world it
    was built from.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    added: list[str] = Field(
        description=(
            "The agents this server can serve now and could not before, sorted. A "
            "device bound to one of them reaches it at its next check-in."
        )
    )
    removed: list[str] = Field(
        description=(
            "The agents this server can no longer be asked for, sorted. A conversation "
            "already talking as one of them finishes on the world it was built with."
        )
    )
    defaults_changed: bool = Field(
        description=(
            "Whether `agent_defaults` moved, which is a boolean because there is one of "
            "it for the whole deployment and nothing to name. What it changes reaches "
            "every agent that inherits the field that moved."
        )
    )


class ConfigReloadResult(BaseModel):
    """What one reload applied to this running server, kind by kind, and
    what is running once it had been applied.

    One request applies a stored change and says what the change was, so
    believing a write took effect when it did not takes a deliberate act
    of not reading the reply. Every section answers: a reload applies
    the whole domain half, so there is no kind left whose section would
    have to say that this build does not touch it. The four that are
    declared optional stay optional in the schema, because a client
    generated against an earlier one is holding a document that says
    they may be null and narrowing that is a change to the contract for
    no gain.
    """

    model_config = ConfigDict(extra="forbid")

    mcp: McpReloadResult
    prompts: PromptsReload
    fillers: FillersReload | None = None
    providers: ProvidersReload | None = None
    agents: AgentsReload | None = None


# Which agents the server around this application can be asked for, as
# a question rather than a set. It used to be a set, because a restart
# was what loaded an agent and the answer could not move while the
# process ran; an apply installs the stored agent set now, so a snapshot
# taken when this application was built would speak for a world that has
# been replaced (#191). What it closes over is the generation holder,
# which is the composition root's business and not this API's. The empty
# answer is honest for an application with no server around it: it can
# serve no agent at all.
type ServableAgents = Callable[[], frozenset[str]]


# And what applies a re-read of the stored configuration to this
# running server: a callable, because what it closes over (the
# generation holder, the MCP managers, and where the blocking re-read
# runs) is the composition root's business and not this API's. It
# answers the whole of the reload's reply, composed where the phases
# live, so the handler applies a configuration and adds nothing to what
# came back. None is the honest answer for an application without a
# server, and the route refuses rather than pretending to have applied
# something.
type ConfigReloader = Callable[[], Awaitable[ConfigReloadResult]]


class PromptBlock(BaseModel):
    """One block of an assembled system prompt."""

    model_config = ConfigDict(extra="forbid")

    provenance: str = Field(
        description=(
            "Where this block came from, as a fixed token this server owns: `persona` "
            "for the agent's own prompt, `fragment:<name>` for a shared prompt "
            "fragment the agent includes, `instructions:<entry>` for the guidance "
            "written on an MCP server entry the agent is granted, "
            "`server_instructions:<entry>` for the guidance that server ships about "
            "itself where the entry opted into it, `server_prompt:<entry>:<position>` "
            "for one of the prompts it publishes, at its position in that entry's "
            "`inject_prompts` counted from one, `memory` for what the agent remembers. "
            "The fragment and entry names are the operator's, and both have been "
            "through the rule that keeps them to `[A-Za-z0-9_-]+`; a published prompt "
            "is identified by its position rather than by its name, because the name "
            "is a string the server chose and this token is printed in logs."
        )
    )
    name: str | None = Field(
        default=None,
        description=(
            "The name the entry's `inject_prompts` gave the published prompt this "
            "block came from, and null for every other kind of block. It is carried "
            "here and not in the provenance because it is a server-chosen string the "
            "operator copied into their configuration, so nothing bounds what it "
            "holds; this body is JSON-encoded and is one of the two places "
            "operator-written configuration is echoed back, which the tokens printed "
            "in logs and structured events are not."
        ),
    )
    characters: int = Field(
        description=(
            "How long this block is, in characters, counted on what is stored and "
            "sent. It is what an operator tunes a small model's context budget "
            "against, block by block."
        )
    )
    text: str = Field(
        description=(
            "The block as the model receives it, heading included, whoever wrote it: a "
            "surface that hid part of the prompt would fail its own purpose, which is "
            "to say what the model was given, and an entry that opted into a server's "
            "own guidance opted those bytes into this prompt. The provenance beside it "
            "is what says whose words they are."
        )
    )


class AssembledPrompt(BaseModel):
    """The system prompt a session opening now as this agent would be
    sent."""

    model_config = ConfigDict(extra="forbid")

    blocks: list[PromptBlock] = Field(
        description=(
            "The blocks in the order they are sent, joined by one blank line each: the "
            "persona, then the shared fragments the agent includes in the order its "
            "layer lists them, then the guidance of each MCP entry the agent is granted "
            "in grant order, and within one entry what its operator wrote, then what "
            "the server ships about itself, then the prompts it publishes in the order "
            "the entry names them, and then the remembered facts. The order is fixed "
            "and not configurable. A block that would hold nothing is not sent and is "
            "not listed, which is what an agent with no prompt of its own produces."
        )
    )
    characters: int = Field(
        description=(
            "The whole prompt's length in characters, which is the sum of the blocks "
            "plus the blank line between each pair of them. The prompt is the blocks "
            "joined and nothing else, so a character counted here is a character the "
            "model receives."
        )
    )


# What the database holds that the running server is not serving
#
# Declared here rather than in `config/diff.py`, which computes them, for
# the reason `McpReloadResult` is declared here rather than in the
# reload that composes one: two surfaces know these shapes and only one
# of them may pay for FastAPI. The comparison imports them and answers
# in them, so the route sends what the comparison built and adds
# nothing, and a field this server starts answering with is a field the
# committed document declares.
#
# Names and closed tokens, by construction: no entity bodies, no values,
# no masks and no secret marks. There is nothing here to filter, which
# is what makes the no-leak claim structural rather than careful.
#
# A field whose type is one of these models or the token enum carries no
# description of its own, and that is deliberate: a description written
# beside a `$ref` is a sibling of it, which some readers drop and the
# rest find confusing, and this document has none anywhere else. What
# would have been said per field is said in the docstring of the model
# it points at, which is the component description a reader lands on.


class Applies(StrEnum):
    """When a change of this kind reaches a conversation.

    Three boundaries and no fourth, because the server has three.
    `restart` is what this process reads once and serves until it is
    started again, which is now the file half alone: the port, the
    directories, the limits and everything else `server` holds, none of
    which this API writes. `reload` is what `POST
    /runtime/config/reload` applies while the process runs, which is the
    whole domain half: the provider entries, the MCP entries, the shared
    prompt fragments, the agents, and `agent_defaults`. `check-in` is
    what a device is answered as it asks, the bindings and the default
    agent, which are therefore in effect within seconds of a write and
    never pending at all.
    """

    RESTART = "restart"
    RELOAD = "reload"
    CHECK_IN = "check-in"


class EntityDiff(BaseModel):
    """One kind of named entity, as the difference between two worlds:
    what the database holds that the running server does not, what it no
    longer holds, and what both have under one name and disagree about.

    `applies` is where this kind's changes converge, so a consumer
    rendering a pending indicator reads what to say from the answer
    rather than knowing it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    applies: Applies
    added: tuple[str, ...] = Field(
        description=(
            "The names the database holds that this server is not serving, sorted."
        )
    )
    removed: tuple[str, ...] = Field(
        description=(
            "The names this server is serving that the database no longer holds, sorted."
        )
    )
    changed: tuple[str, ...] = Field(
        description=(
            "The names both sides have and disagree about, sorted. Changed means the "
            "stored state differs from what this server is serving, never that "
            "something was written: an edit changed back before anyone looked is not "
            "here, and an entity whose stored credential was set again is, because "
            "what is compared is an opaque mark over the ciphertext and that moves "
            "even when the plaintext may not have."
        )
    )


class GrantsDiff(BaseModel):
    """The agents whose effective MCP grants the stored configuration
    would move, which is the half of an agent entry a conversation meets
    at its next utterance rather than at its next activation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    applies: Applies
    changed: tuple[str, ...] = Field(
        description=(
            "The agents that would reach a different set of MCP tools once the stored "
            "configuration is applied, sorted. Compared through the same "
            "defaults-then-own rule the server derives an agent's grants by, so moving "
            "a grant between `agent_defaults` and an agent without changing what that "
            "agent reaches is not a change. Only the agents this server is serving are "
            "compared: one the database has added rides `added` above until a reload "
            "installs it, and one the database no longer holds is here while a reload "
            "would still revoke its tools."
        )
    )


class PromptDiff(BaseModel):
    """The agents whose prompt text the stored configuration would move,
    which is the half of an agent entry a reload has assembled again at
    the next activation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    applies: Applies
    changed: tuple[str, ...] = Field(
        description=(
            "The agents whose stored `prompt` or `prompt_includes` differs from what "
            "this server is serving, sorted. A reload applies both, and each agent that "
            "moved assembles the new text at its next activation. Only agents both "
            "sides hold are compared: an agent the database has added or deleted rides "
            "the added or removed lists above, which a reload applies whole, and "
            "nothing about its prompt is separately pending here. The "
            "fragments the includes name are their own kind, and an edit to a "
            "fragment's text is reported there rather than against every agent that "
            "carries it."
        )
    )


class FillerDiff(BaseModel):
    """The agents whose filled pauses the stored configuration would
    move, which is the half of an agent entry a reload synthesizes again
    and the next conversation opens on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    applies: Applies
    changed: tuple[str, ...] = Field(
        description=(
            "The agents whose stored `filler` section differs from what this server is "
            "serving, sorted. A reload synthesizes the new clips in each agent's own "
            "voice and the next session plays them; a conversation already open goes on "
            "masking with the clips it opened on. Only agents both sides hold are "
            "compared, for the reason the prompt half gives, and only each agent's own "
            "section: what `agent_defaults.filler` holds is inherited by every agent "
            "that configures none of its own, and an edit to it is reported against "
            "`agent_defaults` instead."
        )
    )


class AgentsDiff(EntityDiff):
    """The agents. A reload applies the whole entry, so `changed` above
    compares the whole entry, and the three entries below break that one
    answer into the moments a conversation meets each part at: `grants`
    at its next utterance, `prompt` at its next activation, `filler` when
    the next conversation opens.

    A breakdown and not an exception: an agent whose prompt alone moved
    is in `changed` and in `prompt`, which is one change reported at the
    altitude an operator asks about it. The three carry `reload` for the
    same reason the kind does, and they exist because the clocks differ
    and a single label cannot say which one an edit is on.
    """

    grants: GrantsDiff
    prompt: PromptDiff
    filler: FillerDiff


class SingletonDiff(BaseModel):
    """A kind there is exactly one of, which therefore has nothing to
    name: it moved or it did not.

    Compared whole, grants included: a reload applies the whole layer,
    and what an edit to it reaches is every agent that inherits the
    field that moved. Which agents those are is reported beside this,
    under the agents' own three entries.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    applies: Applies
    changed: bool = Field(
        description=(
            "Whether the stored entry differs from the one this server is serving. A "
            "boolean rather than name lists, because there is one of these for the "
            "whole deployment and nothing to name."
        )
    )


class LiveKind(BaseModel):
    """A kind the running server reads as a device asks for it,
    answered with its label and no comparison.

    Deliberately no lists. What is stored for a device binding or for
    the default agent is served by the entity reads and is in effect by
    that device's next check-in, so a `changed` here would dress a fact
    that is not pending as a diff. The label is what keeps the knowledge
    of why in this server rather than in every consumer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    applies: Applies


class ConfigDiff(BaseModel):
    """What the database holds that the running server is not serving,
    kind by kind, in the order the domain declares them.

    Two of the kinds are worth a word beyond their shapes. A provider is
    addressed as `<stage>.<name>`, the identity the store keeps its
    credentials under and the one every refusal prints, and one whose
    stored credential was rotated is changed, since what it talks to
    moved as surely as if a field of it had. The MCP entries are
    compared against the entries running now rather than the ones this
    process booted with, because the reload swaps that world while the
    process runs: an entry no agent references is compared like any
    other, so is one whose only edit is to the prompt fields a
    connection never sees, and a change a reload has already applied is
    not reported as pending. That last property is the whole of what
    this read is for, and it now holds for every kind: an apply installs
    the stored domain half whole, so a comparison taken after one is
    empty.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    providers: EntityDiff
    mcp_servers: EntityDiff
    prompt_fragments: EntityDiff
    agent_defaults: SingletonDiff
    agents: AgentsDiff
    devices: LiveKind
    default_agent: LiveKind


# And what answers it: a callable, because what it closes over (the
# holder whose generation is what this server is serving, the
# credentials loaded with that world, and the registry whose managers a
# reload replaces) is the composition root's business and not this
# API's. It answers the whole comparison,
# composed where the two worlds are, so the handler awaits it and adds
# nothing. None is the honest answer for an application without a
# server, and the route refuses rather than reporting an empty diff,
# which would say that nothing is pending.
type ConfigDiffReader = Callable[[], Awaitable[ConfigDiff]]


class RuntimeInfo(BaseModel):
    """Which deployment this is: the build that is running, and the URL
    a board is onboarded at.

    The one answer to "what server am I talking to". Every field of it
    is a fact of the process and of the file half it booted from,
    neither of which a reload moves, so the whole of it is composed once
    at startup and answered as it stands.

    It carries a credential, and it is the only read here that does. The
    onboarding URL's last segment is a key derived from the device-auth
    secret and it stands in front of the token issuer, which is why the
    startup banner deliberately prints the origin and not the URL. The
    gate in front of this API is what makes serving it here no wider
    than what the caller already holds: the bearer token grants
    everything this API can do, secret writes included. The response
    carries `Cache-Control: no-store` for the same reason, and the one
    client that renders it prints it on stdout and nowhere else.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(
        description=(
            "The package version of the server answering, which says what this is "
            "rather than which build of it."
        )
    )
    revision: str = Field(
        description=(
            "Which build of it: the revision baked into the image, or what `git "
            "describe` says of the checkout it runs from, or `unknown` when neither "
            "is there to read. It is what matches a running server to the change that "
            "produced it."
        )
    )
    onboarding_enabled: bool = Field(
        description=(
            "Whether this deployment serves the short onboarding path a person can "
            "type into a device's captive portal (`server.onboarding.enabled`). False "
            "leaves the two fields below null: devices are configured at the path "
            "`server.ota_path` names, which is this deployment's secret and is not "
            "answered here."
        )
    )
    onboarding_url: str | None = Field(
        description=(
            "The URL to type into a device's captive portal, or null when onboarding "
            "is off. It is the origin this deployment names itself by and the derived "
            "key after it, the same value `vinga ota-url` prints from the file half "
            "alone, so a deployment names itself identically wherever it is named. It "
            "is a credential: the key stands in front of the token issuer."
        )
    )
    onboarding_provenance: str | None = Field(
        description=(
            "Where the origin in that URL came from, in the words the startup banner "
            "uses (`from server.public_url`, `guessed from the listen address, ...`), "
            "or null when onboarding is off. Two of the three sources are inferences, "
            "so a URL that named neither would read as fact."
        )
    )


class DefaultAgent(BaseModel):
    """The agent an unbound device reaches."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        description=(
            "The default agent's name, or null when none is set, which leaves the "
            "devices map as the allowlist."
        ),
    )


class FieldError(BaseModel):
    """One field of the submitted body that a refusal names."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        description=(
            "Which part of the submitted body this is about, as an RFC 6901 JSON "
            "Pointer into it: `/filler/phrases` is that field of that object, `/mcp/0` "
            "is that position of that list, and the empty string is the body as a "
            "whole. Only names this server declares and positions appear in it. A key "
            "the request invented is never one of them: an unrecognized key, an option "
            "a provider passes through, an entry of an `env` or `headers` map. A key is "
            "as good a place to paste a credential as a value is, so the pointer stops "
            "at the nearest enclosing place this server can name, which for a key "
            "written at the top of a fragment is the fragment itself."
        )
    )
    message: str = Field(
        description=(
            "What is wrong with it, in the same words the corresponding line of "
            "`detail` uses: the two are rendered from one computation, so a client "
            "showing this beside the field and one showing the whole sentence say the "
            "same thing. Like `detail`, it names a rule and never quotes the value it "
            "rejected or a key the request invented. Where the rule is about such a "
            "key, it names what made the key match instead, which is a word from a "
            "closed list this server owns."
        )
    )


# What a refusal is served as: RFC 9457's own media type, so a client
# can tell a refusal this API wrote from a page a proxy in front of it
# did without reading either.
PROBLEM_MEDIA_TYPE = "application/problem+json"

# And what a refusal of each status is called: the status's standard HTTP
# reason phrase, which is what RFC 9457 asks a problem with no `type` to
# carry.
#
# Here rather than beside the descriptions the document renders, because
# this is the half a client reads too: `config/cli.py` believes a
# `detail` only from a body that is this shape, under this media type,
# carrying this title for the status it arrived under, and a second copy
# of the phrases would be a second thing to keep true. The phrases are
# not taken from `http.HTTPStatus` for the same reason they are written
# out at all: 422 is `Unprocessable Content` here and the standard
# library of the interpreter this runs on still calls it
# `Unprocessable Entity`.
PROBLEM_TITLES: dict[int, str] = {
    401: "Unauthorized",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    422: "Unprocessable Content",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


class Problem(BaseModel):
    """A refusal, as RFC 9457 problem details.

    Served as `application/problem+json`. `type` and `instance` are
    deliberately absent: an absent `type` means `about:blank`, which is
    the truth here, since these problems are described by their status
    and their prose rather than by a URI registry nobody serves.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        description=(
            "The status code's standard HTTP reason phrase, such as `Not Found`. It is "
            "the phrase and not a description of this API's own, because with `type` "
            "absent the problem type is `about:blank`, whose title RFC 9457 says should "
            "be the status's recommended phrase; a title of this server's own would "
            "imply a problem type the body does not identify. What was actually refused "
            "is `detail`."
        )
    )
    status: int = Field(
        description=(
            "The HTTP status code, repeated in the body as the RFC describes, so that a "
            "problem separated from its response (in a log, in a bug report) still says "
            "what it was answered under."
        )
    )
    detail: str = Field(
        description=(
            "What was refused and why, the same sentence the `vinga-server config` "
            "command prints for it. It names the entity the request addressed and "
            "the rule that was broken; it never quotes a secret, a configuration "
            "value that was rejected, or a key the request invented."
        )
    )
    errors: list[FieldError] = Field(
        description=(
            "The fields of the submitted body the refusal names, one entry each, for a "
            "client that wants to mark the offending field rather than show a "
            "paragraph. Present on every refusal and empty where there is no field to "
            "name: a body whose whole shape was wrong, a reference to another entity, a "
            "failure that was not the caller's. The entries are the same problems "
            "`detail` lists, in the same order and the same words."
        )
    )


class Acknowledgement(BaseModel):
    """What a write answers with: what it did, and when it takes
    effect."""

    model_config = ConfigDict(extra="forbid")

    wrote: str = Field(
        description=(
            "What was written or deleted, naming the entity the way the "
            "`vinga-server config` command names it in the line it prints."
        )
    )
    notice: str = Field(
        description=(
            "When the change takes effect, as one of a handful of sentences. A device "
            "binding and the default agent are read by the running server as a device "
            "asks for them, so they apply at that device's next OTA check or "
            "connection with nothing asked of the server. Every other kind this API "
            "writes, which is the whole of the rest of the domain half (the provider "
            "entries, the MCP entries, the secret slots on either, the prompt "
            "fragments, the agents and `agent_defaults`), names `POST "
            "/runtime/config/reload`, which applies it without a restart; that "
            "sentence also names the three moments a conversation already in progress "
            "meets an applied change at, its tools at the next utterance, its prompt "
            "text at the next activation, and the voice and filled pauses at the next "
            "conversation. A binding naming an agent this server is not serving yet "
            "carries a sentence of its own, because both halves are true at once: the "
            "row is live, and the agent arrives at the reload that installs it. A "
            "server serving a configuration no store describes says that the write is "
            "stored and takes effect when a server boots from that store. Nothing this "
            "API writes waits for a server start."
        )
    )


class AppliedEntry(BaseModel):
    """What applying one entry of a document did."""

    model_config = ConfigDict(extra="forbid")

    section: str = Field(
        description=(
            "Which section of the domain configuration the entry is in: one of "
            "`providers`, `mcp_servers`, `prompt_fragments`, `agent_defaults`, "
            "`agents`, `devices`, `default_agent`. The entries are listed in that "
            "order, which is the order the configuration documents as its write, read "
            "and creation order."
        )
    )
    identity: str = Field(
        description=(
            "The entry's identity under that section, as the row holds it rather than "
            "as the document spelled it: `<stage>.<name>` for a provider, the "
            "canonical form of the MAC for a device, the name for everything else. "
            "Empty for `agent_defaults` and `default_agent`, which hold one thing "
            "rather than entries."
        )
    )
    outcome: Literal["wrote", "unchanged"] = Field(
        description=(
            "Whether the row moved. `unchanged` means the entry was already exactly "
            "what the document says, so nothing was written for it and nothing has to "
            "be applied: the same document sent twice is a no-op by construction."
        )
    )
    notice: str | None = Field(
        description=(
            "When this entry's change takes effect, in the same sentences a single "
            "write of it is acknowledged with, and null for an entry that was "
            "`unchanged`, which has nothing waiting to be applied."
        )
    )


class AppliedDocument(BaseModel):
    """What applying one document did, entry by entry."""

    model_config = ConfigDict(extra="forbid")

    entries: list[AppliedEntry] = Field(
        description=(
            "One entry per thing the document named, in the configuration's own "
            "section order. Empty for a document that named nothing. A document is "
            "written whole or not at all, so a 200 means every entry below was "
            "applied and any refusal means none of them was."
        )
    )


# The three bodies that are arguments rather than fragments, as the
# document describes them. The models below are documentation and
# nothing else: `api.py` injects them into `components` and names them
# from the routes' `openapi_extra`, and they are deliberately not
# declared as body types, for the reason the entity models are not
# either. What enforces them at runtime is that module's exact-shape
# parser, which describes the expectation and never echoes what it
# refused.


class DeviceBinding(BaseModel):
    """What a device write carries: the agents the device may reach."""

    model_config = ConfigDict(extra="forbid")

    agents: list[str] = Field(
        description=(
            "The agents this device is bound to, by name. The first is the agent a "
            "conversation starts on and the rest are the ones switch_agent can reach. "
            "Every name has to be an agent that exists, or the write is refused."
        )
    )


class DefaultAgentName(BaseModel):
    """What a default-agent write carries. Clearing it is the DELETE,
    not a null here: one way to say a thing."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description=(
            "The agent an unbound device reaches. It has to be an agent that exists. "
            "To unset it, DELETE this resource, which leaves the devices map as the "
            "allowlist."
        )
    )


class SecretValue(BaseModel):
    """What a secret write carries: the credential itself, the only
    plaintext this API ever accepts."""

    model_config = ConfigDict(extra="forbid")

    secret: str = Field(
        # The runtime parser refuses an empty string, and so does the
        # repository underneath it, so the document says the same: a
        # contract that permits what the API refuses is one a client
        # generator would build the wrong request from.
        min_length=1,
        description=(
            "The credential, in plaintext, stored encrypted under the newest key in "
            "VINGA_MASTER_KEY. It crosses the connection as itself, which is why the "
            "whole API belongs on a loopback connection or behind TLS. It is never "
            "read back: a read names the slot and masks the value. It may not be "
            "empty."
        ),
    )


# The conversation store's session shapes
#
# Here rather than beside the routes that answer them, for the reason
# every other model on this page is here: two surfaces know these shapes
# and only one of them may pay for FastAPI. `vinga session list` and
# `vinga session show` read an answer by validating it against the shape
# the API said it would send, and a CLI that imported
# `conversations/api.py` to find out would import FastAPI, SQLAlchemy and
# the whole store with it.
#
# `CLOSE_REASONS` is written out rather than read off
# `conversations/schema.py`, which is the same trade the mask above
# makes and for the same reason: this module imports nothing of this
# server. The two are held equal from the outside, by the pin in
# `test_api_openapi.py` that compares the rendered document's enum
# against the schema's own tuple.

CLOSE_REASONS = ("limit", "idle", "drain", "client", "error")

CloseReason = Literal[*CLOSE_REASONS]


class SessionSummary(BaseModel):
    """One session as the listing shows it."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(
        description=(
            "The session's monotonic row id, never reused. It is this listing's "
            "cursor: a page asked for with it holds the sessions before it."
        )
    )
    session: str = Field(
        description=(
            "The session's uuid hex, which addresses the two reads below and names the "
            "capture triplet of the same conversation."
        )
    )
    device: str | None = Field(
        description=(
            "The device's MAC in canonical form, and null when the session was "
            "rejected before one was understood."
        )
    )
    agent: str | None = Field(
        description="The agent the session opened with, before any handover."
    )
    started_at: str = Field(description="When the session opened, as an ISO-8601 instant in UTC.")
    closed_at: str | None = Field(
        description=(
            "When it closed, in the same form. Null in a session that is still running "
            "and in one whose close was never persisted, which a crash leaves behind."
        )
    )
    duration_s: float | None = Field(
        description="How long it lasted, in seconds. A measured number: null under metrics-off."
    )
    close_reason: CloseReason | str | None = Field(
        description=(
            "What ended it, one of `limit`, `idle`, `drain`, `client` or `error`, the "
            "first cause to fire winning. Null until it closes. The five tokens are "
            "the set a server latches, and the column that holds them is deliberately "
            "unconstrained, so a token a later release adds is served as it was "
            "stored: a read that refused one would drop a whole page over one row, "
            "the same reason the database refuses none."
        )
    )
    turns: int = Field(description="How many turns this session holds.")


class SessionList(BaseModel):
    """One page of the session listing, newest first."""

    model_config = ConfigDict(extra="forbid")

    items: list[SessionSummary] = Field(
        description="The sessions on this page, newest first."
    )
    next_cursor: int | None = Field(
        description=(
            "What to send as `cursor` for the page after this one, and null when this "
            "was the last: it is the id of the last item here, and the next page holds "
            "the sessions below it. Null means there is nothing further right now, not "
            "that there never will be; a listing re-read later starts from the top."
        )
    )


class SessionDetail(BaseModel):
    """One session, whole: its row and what hangs off it."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(description="The session's monotonic row id, the listing's cursor.")
    session: str = Field(description="The session's uuid hex.")
    device: str | None = Field(
        description="The device's MAC in canonical form, or null when none was understood."
    )
    client: str | None = Field(
        description="The client identifier the device announced, when it announced one."
    )
    agent: str | None = Field(
        description="The agent the session opened with, before any handover."
    )
    agents: list[str] | None = Field(
        description=(
            "Every agent the device is bound to, by name, as the binding resolved at "
            "open. The first is the agent the session started on."
        )
    )
    protocol: str | None = Field(
        description="The device protocol version this session negotiated."
    )
    started_at: str = Field(description="When the session opened, as an ISO-8601 instant in UTC.")
    closed_at: str | None = Field(description="When it closed, in the same form, or null.")
    duration_s: float | None = Field(
        description="How long it lasted, in seconds. Null under metrics-off."
    )
    close_reason: CloseReason | str | None = Field(
        description=(
            "What ended it, one of the five tokens the listing describes, or null "
            "until it closes."
        )
    )
    server_version: str | None = Field(
        description="The server version that recorded this session."
    )
    revision: str | None = Field(description="The build revision that recorded it.")
    providers: dict[str, Any] | None = Field(
        description=(
            "The resolved provider entry per pipeline stage, the same structure the "
            "capture manifest carries. It holds environment variable names, never "
            "credentials."
        )
    )
    metrics: bool = Field(
        description=(
            "Whether metrics storage was on for this session, so a null number is "
            "distinguishable from a number that was never stored."
        )
    )
    text: bool = Field(
        description=(
            "Whether text storage was on for this session, so a null utterance is "
            "distinguishable from an utterance that was never stored."
        )
    )
    dropped: int = Field(
        description=(
            "Records this session lost: events refused at the writer's in-flight bound, "
            "and anything a failed transaction rolled back. The store recording its own "
            "incompleteness, the way the capture manifest records `complete`."
        )
    )
    turns: int = Field(description="How many turns this session holds.")
    events: int = Field(
        description=(
            "How many events rows it holds: the decision track, `session_open` through "
            "`session_closed`. Zero under metrics-off, where no events row lands. They "
            "are deliberately not served over REST; the database is that surface."
        )
    )


class Erasure(BaseModel):
    """What a deletion took, per table.

    Counts rather than an acknowledgement sentence, because the caller
    of a purge asked about a set it named by selector and cannot know
    what was in it. Six numbers rather than one, because the tables go
    for different reasons: a session's own row, the turns that named it
    wherever their thread is, the invocations under those turns, the
    session's telemetry, the recap checkpoints whose coverage held an
    erased turn, and the threads left with nothing.
    """

    model_config = ConfigDict(extra="forbid")

    sessions: int = Field(
        description="How many session rows were deleted, at most one per selector match."
    )
    turns: int = Field(
        description=(
            "How many turns went with them, including turns of a thread that is still "
            "live: erasing a session erases its dialogue wherever it belongs, and the "
            "thread honestly keeps a gap."
        )
    )
    tool_invocations: int = Field(
        description="How many tool invocation rows hung off those turns."
    )
    events: int = Field(
        description=(
            "How many events rows those sessions held. Zero for a session recorded "
            "under metrics-off, which wrote none."
        )
    )
    conversations: int = Field(
        description=(
            "How many threads were deleted whole because they lost every turn. A "
            "thread that kept turns is not counted here: it survives, renamed from "
            "its earliest surviving turn if the one its title came from is gone."
        )
    )
    milestones: int = Field(
        description=(
            "How many recap checkpoints were deleted: those whose recorded coverage "
            "held an erased turn, every checkpoint descended from one along the "
            "`parent` lineage, and those belonging to a thread that went whole."
        )
    )


# The conversation store's turn shapes
#
# Here rather than beside the routes for the reason the session shapes
# above are, and they arrived later for a reason worth recording: while
# no command read a turn timeline they could stay in
# `conversations/api.py`, and `vinga conversation show` reads one. So
# they moved when the reason to leave them expired.
#
# `TOOL_SOURCES` is written out here rather than read off
# `conversations/schema.py`, the trade this module's own docstring makes
# for the mask and the close reasons and for the same reason: it imports
# nothing of this server. The pin in `test_api_openapi.py` holds this
# spelling and the schema's own tuple equal through the rendered
# document, which is the byte a client reads.

TOOL_SOURCES = ("builtin", "device", "mcp", "unknown")

ToolSource = Literal[*TOOL_SOURCES]


class ToolInvocation(BaseModel):
    """One call a turn issued, as the timeline nests it.

    The transport shape of a `tool_invocations` row, not the record the
    pipeline hands the writer, which is the dataclass of the same name
    in `records.py`.
    """

    model_config = ConfigDict(extra="forbid")

    position: int = Field(
        description=(
            "Where this call sat in the round's call list, as the model issued it, "
            "counted from zero and with handovers included. The rows are nested in "
            "this order; it is the model's order and not the order they finished in."
        )
    )
    source: ToolSource = Field(
        description=(
            "Where the call was routed: `builtin` for one this application authors, "
            "`device` for one the device published, `mcp` for one an MCP entry owns, "
            "`unknown` for a name nothing answered to. Classified before the call ran. "
            "A closed set here because it is a closed set in the database, which holds "
            "the same four tokens under a check constraint."
        )
    )
    entry: str | None = Field(
        description=(
            "The owning MCP entry's configured name for an `mcp` call, and null "
            "otherwise. A name this deployment chose, so it survives text-off."
        )
    )
    name: str | None = Field(
        description=(
            "The called tool's name, and null when text storage was off for the "
            "session: a tool's name originates off this server, a device's "
            "self-description or an MCP far side, exactly as its result does."
        )
    )
    malformed: bool = Field(
        description="Whether the model's arguments were not a JSON object."
    )
    arguments: dict[str, Any] | None = Field(
        description=(
            "What the model passed, null under text-off and null when the call was "
            "malformed, which is what `malformed` above tells them apart by."
        )
    )
    result: str | None = Field(
        description="What the call answered, a refusal included. Null under text-off."
    )
    is_error: bool = Field(description="Whether the call answered as an error.")
    duration_ms: int | None = Field(
        description=(
            "How long the call took, in milliseconds. Null where nothing ran, as for a "
            "refused or a successful handover, and null under metrics-off."
        )
    )


class TurnLeg(BaseModel):
    """One agent's share of a turn a handover split.

    The transport shape of one entry of `turns.legs`, whose halves
    follow different storage switches: the text is content and the token
    counts are measurements, which is why a leg exists at all rather
    than the turn's totals being the whole story. The totals blend
    agents that may use different models; this is where they come apart
    again.
    """

    model_config = ConfigDict(extra="forbid")

    agent: str | None = Field(
        description=(
            "The agent whose leg this is, and null for a session that never activated "
            "one."
        )
    )
    text: str | None = Field(
        description=(
            "What this agent said, null under text-off, and null for an agent that "
            "took part without speaking: one that asked for the handover said nothing "
            "and spent tokens all the same."
        )
    )
    input_tokens: int | None = Field(
        description=(
            "Input tokens this agent spent on the turn. Null when the provider "
            "reported no usage, and under metrics-off."
        )
    )
    output_tokens: int | None = Field(
        description=(
            "Output tokens this agent spent on the turn. Null when the provider "
            "reported no usage, and under metrics-off."
        )
    )


class SessionTurn(BaseModel):
    """One utterance and the reply it got, with the calls the reply made."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(
        description=(
            "The turn's monotonic row id, never reused. It is this timeline's cursor: "
            "a page asked for with it holds the turns after it."
        )
    )
    conversation: str = Field(
        description=(
            "The thread this turn belongs to, by its uuid hex. A turn names both "
            "its session and its conversation, which is what makes the session "
            "timeline and the thread two readings of one set of rows: the turns of "
            "one session can belong to several threads, and one thread's turns can "
            "come from several sessions."
        )
    )
    t_ms: int = Field(
        description=(
            "The utterance's offset from session open, in milliseconds, aligned with "
            "its `heard` event and with the capture's audio for the same session."
        )
    )
    agent: str | None = Field(
        description=(
            "The agent that owns this turn, which is the one it started with and "
            "therefore the one whose thread it is on. A handover makes it different "
            "from the session's and from the agent that finished the reply; the legs "
            "below are where a split reply comes apart."
        )
    )
    heard: str | None = Field(
        description="What was said to the device, as transcribed. Null under text-off."
    )
    heard_duration_s: float | None = Field(
        description="How long the utterance lasted, in seconds. Null under metrics-off."
    )
    language: str | None = Field(
        description=(
            "The language the transcript was recognized as. Neither a measured number "
            "nor conversation text, so it survives both switches."
        )
    )
    language_confidence: float | None = Field(
        description="How sure the recognizer was of that language. Null under metrics-off."
    )
    reply: str | None = Field(
        description=(
            "What the assistant said, the legs joined. Null under text-off, and null "
            "when the reply spoke nothing."
        )
    )
    legs: list[TurnLeg] | None = Field(
        description=(
            "One entry per agent that took part, and present only when a handover "
            "split the reply. Null is a turn one agent answered whole, which is not "
            "the same as an empty list and never becomes one."
        )
    )
    asr_ms: int | None = Field(
        description=(
            "Transcription elapsed, in milliseconds. Null where none was measured this "
            "turn, and under metrics-off."
        )
    )
    first_token_ms: int | None = Field(
        description="Request to the reply's first token, in milliseconds. Null under metrics-off."
    )
    llm_ms: int | None = Field(
        description=(
            "The reply's LLM round durations summed, in milliseconds. Null under "
            "metrics-off."
        )
    )
    tts_first_audio_ms: int | None = Field(
        description=(
            "The reply's first synthesis request to its first audio bytes, in "
            "milliseconds, measured at the provider boundary and deliberately not at "
            "the device. Null when the reply spoke nothing, and under metrics-off."
        )
    )
    rounds: int | None = Field(
        description="How many LLM rounds the reply took. Null under metrics-off."
    )
    input_tokens: int | None = Field(
        description=(
            "Input tokens summed across the turn's rounds; OTel's "
            "`gen_ai.usage.input_tokens`. Null when the provider reported no usage, "
            "and under metrics-off."
        )
    )
    output_tokens: int | None = Field(
        description=(
            "Output tokens summed across the turn's rounds; OTel's "
            "`gen_ai.usage.output_tokens`. Null when the provider reported no usage, "
            "and under metrics-off."
        )
    )
    tool_calls: int = Field(
        description=(
            "How many calls this turn issued, which is how many entries the list below "
            "holds. Structural rather than telemetry: it survives both switches."
        )
    )
    tool_invocations: list[ToolInvocation] = Field(
        description="The calls the reply made, in the order the model issued them."
    )


class SessionTurns(BaseModel):
    """One page of a session's timeline, oldest first."""

    model_config = ConfigDict(extra="forbid")

    items: list[SessionTurn] = Field(
        description="The turns on this page, ascending by id, which is chronological."
    )
    next_cursor: int | None = Field(
        description=(
            "What to send as `cursor` for the page after this one, and null when this "
            "was the last. The next page holds the turns after it, which is also how a "
            "client that read up to a turn asks for what has happened since."
        )
    )


# The conversation store's thread shapes
#
# A thread is the other projection of the same rows: the session read
# answers one connection episode, and these answer one durable
# conversation, which may span several of them. The turn shape below is
# the session's with the session added rather than a second copy of
# twenty descriptions, because two structures that must agree are one
# structure with a bug pending.


class ConversationSummary(BaseModel):
    """One thread as the listing shows it."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(
        description=(
            "The thread's monotonic row id, never reused. It is the second half of "
            "this listing's cursor: activity moves, so a page is asked for by the "
            "pair (`last_active_at`, `id`) rather than by a row id alone."
        )
    )
    conversation: str = Field(
        description=(
            "The thread's uuid hex, which addresses the reads and the deletion below "
            "and which every turn of it carries."
        )
    )
    agent: str = Field(
        description=(
            "The agent this thread belongs to. A conversation has exactly one, for "
            "its whole life: a handover starts a thread of the incoming agent's "
            "rather than moving this one."
        )
    )
    title: str | None = Field(
        description=(
            "What the thread is called, which is the earliest utterance stored on "
            "it, bounded. Null where text storage was off when it began, and null "
            "for a thread that has never stored one."
        )
    )
    device: str | None = Field(
        description=(
            "The device the thread was begun on, by MAC. A record of where it "
            "started rather than a binding: a thread is reachable from any device "
            "bound to its agent."
        )
    )
    incomplete: bool = Field(
        description=(
            "Whether a write this thread needed was lost. Product state rather than "
            "telemetry, and deliberately outside the metrics switch that zeroes "
            "`dropped` on a session: a thread with a hole in it is a thread with a "
            "hole in it however the switches are set."
        )
    )
    created_at: str = Field(
        description="When the thread's first turn landed, as an ISO-8601 instant in UTC."
    )
    last_active_at: str = Field(
        description=(
            "When its newest turn landed, in the same form. This is what the listing "
            "orders on and what retention measures, so it moves with every turn. "
            "Where the turn that wrote it has been erased it falls back to a lower "
            "bound, which the store's reference states exactly."
        )
    )
    turns: int = Field(description="How many turns this thread holds, across every session.")


class ConversationList(BaseModel):
    """One page of the thread listing, most recently active first."""

    model_config = ConfigDict(extra="forbid")

    items: list[ConversationSummary] = Field(
        description=(
            "The threads on this page, ordered by `last_active_at` descending with "
            "`id` descending as the tie-break."
        )
    )
    next_cursor_active: str | None = Field(
        description=(
            "What to send as `cursor_active` for the page after this one: the "
            "`last_active_at` of the last item here. Null when this was the last "
            "page, and null together with `next_cursor_id`, which the two request "
            "parameters are also sent together or not at all."
        )
    )
    next_cursor_id: int | None = Field(
        description=(
            "What to send as `cursor_id` beside it: the `id` of the last item here. "
            "Two plain values rather than one opaque cursor, so nothing here is an "
            "encoding a later release has to keep reading."
        )
    )


class ConversationMilestone(BaseModel):
    """One recap checkpoint on a thread.

    What the agent said out loud when the user consented to a recap,
    with the range of turns it summarized and the checkpoint it folded
    into itself. The range is inclusive at both ends and is what keeps
    the claim honest: a recap bounded by its own input budget records
    where its reading began, so nothing here says it covers turns it
    never read.
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(
        description=(
            "The checkpoint's row id, and what a later checkpoint names as its "
            "`parent` when it consumed this one."
        )
    )
    from_turn: int = Field(
        description=(
            "The id of the first turn this recap actually read, and the first it "
            "covers. Coverage is the inclusive range from_turn through "
            "after_turn; turns below from_turn are outside it, dropped by the "
            "recap's own input budget exactly as truncation would have dropped "
            "them."
        )
    )
    after_turn: int = Field(
        description=(
            "The id of the last turn it read, and the last it covers. Rebuilding "
            "this thread's context reads this checkpoint plus the turns with a "
            "greater id."
        )
    )
    parent: int | None = Field(
        description=(
            "The checkpoint whose text was part of this recap's input, and null "
            "when none was. Content that reached this row only through an earlier "
            "recap is still this row's content, which is what makes erasure of it "
            "transitive."
        )
    )
    created_at: str = Field(
        description="When the checkpoint was stored, as an ISO-8601 instant in UTC."
    )
    text: str | None = Field(
        description=(
            "The recap, byte for byte as it was spoken to the user. Conversation "
            "content, so null where text storage was off, although the flow that "
            "writes one cannot run with text off."
        )
    )


class ConversationDetail(ConversationSummary):
    """One thread, whole: the summary's fields and what hangs off it.

    The summary plus its checkpoints, which is the one thing a listing
    leaves out: a thread carries no other nested structure.
    """

    milestones: int = Field(
        description=(
            "How many recap checkpoints this thread holds, which is the length of "
            "`checkpoints` below and is answered beside it so a caller counting "
            "them does not have to."
        )
    )
    checkpoints: list[ConversationMilestone] = Field(
        description=(
            "The recap checkpoints themselves, oldest first. Short by construction: "
            "one lands per recap the user consented to, and they are deleted with "
            "the thread and with the content they summarized."
        )
    )


class ConversationTurn(SessionTurn):
    """One turn as a thread's dialogue answers it.

    The session timeline's turn with the session added. A turn names
    both, which is what makes the two views two readings of one set of
    rows: the turns of one session can belong to several threads, and
    one thread's turns can come from several sessions.
    """

    session: str = Field(
        description=(
            "The session this turn was spoken in, by its uuid hex, which addresses "
            "the session reads and names that session's capture triplet."
        )
    )


class ConversationTurns(BaseModel):
    """One page of a thread's dialogue, oldest first."""

    model_config = ConfigDict(extra="forbid")

    items: list[ConversationTurn] = Field(
        description="The turns on this page, ascending by id, which is chronological."
    )
    next_cursor: int | None = Field(
        description=(
            "What to send as `cursor` for the page after this one, and null when this "
            "was the last. The next page holds the turns after it, which is also how "
            "a client that read up to a turn asks for what has happened since."
        )
    )


class ThreadErasure(BaseModel):
    """What erasing a thread took, per table.

    Four counts and deliberately not the six a session erasure answers:
    erasing a thread takes its turns out of whatever sessions they were
    spoken in and touches neither those sessions nor their telemetry. A
    session is a connection episode and it still happened, with a gap in
    it now.
    """

    model_config = ConfigDict(extra="forbid")

    conversations: int = Field(
        description="How many threads were deleted, which is one for an addressed erasure."
    )
    turns: int = Field(
        description=(
            "How many turns went with them, wherever they were spoken. Their "
            "sessions' own rows are untouched and keep the gap."
        )
    )
    tool_invocations: int = Field(
        description="How many tool invocation rows hung off those turns."
    )
    milestones: int = Field(
        description=(
            "How many recap checkpoints went: the thread's own, and everything "
            "descended from one along the `parent` lineage."
        )
    )
