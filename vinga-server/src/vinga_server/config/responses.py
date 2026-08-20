"""The shapes the configuration API answers and accepts.

Pydantic models, in a module that imports pydantic and nothing else,
which is the whole reason they are not in `api.py`. Two surfaces know
these shapes and only one of them may pay for FastAPI: the API declares
them as its response models, and the CLI renders an answer it received
over HTTP by validating it against the shape the API said it would send
rather than against a hand-kept list of the keys it expects. `writes.py`
records why the CLI must not import the API, and `config schema` and
`config reference` are the commands that would otherwise pay for it.

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
from typing import Any, Literal, Protocol, get_args, get_origin

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
            "here rather than at the next server start."
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


def outcomes(section: type[BaseModel]) -> tuple[str, ...]:
    """One reload section's outcome lists, in the order it declares
    them: every field that is a list of names.

    Presentation, which is why the answer is a tuple and not a set, but
    presentation of the model's own fields: read off the declaration
    rather than listed again, so an outcome added to a section is one
    line on that section and the CLI prints it. What the rule leaves out
    is every field that is not a list of names, which today is the MCP
    status mapping and the agent-defaults flag; each of those is
    rendered where its own shape is understood.
    """
    return tuple(
        name
        for name, field in section.model_fields.items()
        if get_origin(field.annotation) is list and get_args(field.annotation) == (str,)
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
            "connection is what changed. An agent the stored configuration added or "
            "removed is not here either, because the agents this server can serve are "
            "still what it started with."
        )
    )


class FillersReload(BaseModel):
    """What a reload did to the pre-synthesized filled pauses.

    Answered as null until the milestone that makes fillers a reloaded
    kind: this server applies a reload without touching a clip, and an
    empty three-way answer would say it had considered every agent and
    found nothing to do.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    resynthesized: list[str] = Field(
        description=(
            "The agents whose filler clip was made again, because the phrases or the "
            "voice behind them moved, sorted."
        )
    )
    reused: list[str] = Field(
        description=(
            "The agents whose filler clip was carried over unchanged, sorted. Reuse is "
            "the point of the comparison: an edit to a prompt never re-synthesizes a "
            "clip."
        )
    )
    disabled: list[str] = Field(
        description=(
            "The agents whose synthesis failed, sorted. The reload applied and those "
            "agents run with the latency mask off, because a filler is a mask and a "
            "posture where a text-to-speech hiccup blocked a prompt fix would invert "
            "what matters."
        )
    )


class ProvidersReload(BaseModel):
    """What a reload did to the built providers.

    Answered as null until the milestone that makes providers a reloaded
    kind, for the reason the fillers section is.
    """

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

    Answered as null until the milestone that makes the agent set a
    reloaded kind, for the reason the two sections above are.
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
    of not reading the reply. Which kinds a reload can apply grows
    milestone by milestone; the sections below that this server does not
    apply yet answer null rather than an empty answer, because an empty
    three-way answer would claim that every agent was considered and
    nothing needed doing.
    """

    model_config = ConfigDict(extra="forbid")

    mcp: McpReloadResult
    prompts: PromptsReload
    fillers: FillersReload | None = None
    providers: ProvidersReload | None = None
    agents: AgentsReload | None = None


def flags(section: type[BaseModel]) -> tuple[str, ...]:
    """One reload section's yes-or-no answers, in the order it declares
    them.

    The sibling of `outcomes` above and the other half of what a section
    can say: a kind there is one of has nothing to name, so what moved
    about it is a boolean. Read off the declaration for the same reason,
    so that a flag added to a section is a flag the CLI prints.
    """
    return tuple(
        name for name, field in section.model_fields.items() if field.annotation is bool
    )


def _section(annotation: object) -> type[BaseModel]:
    """The model behind one section of the result, whether or not the
    section is optional. A section that is not filled yet is declared
    `Model | None`, and what a renderer needs is the model either way."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return next(
        argument
        for argument in get_args(annotation)
        if isinstance(argument, type) and issubclass(argument, BaseModel)
    )


# Which sections one reload answers with and what shape each of them
# is, read off the result rather than written down beside it: a section
# added to the model is a section the CLI renders, and a field whose
# shape the rendering has no rule for is a failing test rather than
# output that quietly went missing.
RELOAD_SECTIONS: dict[str, type[BaseModel]] = {
    name: _section(field.annotation)
    for name, field in ConfigReloadResult.model_fields.items()
}

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
    started again. `reload` is what `POST /runtime/config/reload`
    applies while the process runs, which is the MCP entries, the
    agents' grants, the shared prompt fragments and each agent's own
    prompt text. `check-in` is what a device is answered as it asks, the
    bindings and the default agent, which are therefore in effect within
    seconds of a write and never pending at all.
    """

    RESTART = "restart"
    RELOAD = "reload"
    CHECK_IN = "check-in"


ADDED_DESCRIPTION = (
    "The names the database holds that this server is not serving, sorted."
)

REMOVED_DESCRIPTION = (
    "The names this server is serving that the database no longer holds, sorted."
)

CHANGED_DESCRIPTION = (
    "The names both sides have and disagree about, sorted. Changed means the stored "
    "state differs from what this server is serving, never that something was "
    "written: an edit changed back before anyone looked is not here, and an entity "
    "whose stored credential was set again is, because what is compared is an opaque "
    "mark over the ciphertext and that moves even when the plaintext may not have."
)


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
    added: tuple[str, ...] = Field(description=ADDED_DESCRIPTION)
    removed: tuple[str, ...] = Field(description=REMOVED_DESCRIPTION)
    changed: tuple[str, ...] = Field(description=CHANGED_DESCRIPTION)


class GrantsDiff(BaseModel):
    """The agents whose effective MCP grants the stored configuration
    would move, which is the half of an agent entry that converges at
    the reload rather than at a restart."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    applies: Applies
    changed: tuple[str, ...] = Field(
        description=(
            "The agents that would reach a different set of MCP tools once the stored "
            "configuration is applied, sorted. Compared through the same "
            "defaults-then-own rule the server derives an agent's grants by, so moving "
            "a grant between `agent_defaults` and an agent without changing what that "
            "agent reaches is not a change. An agent this server loaded and the "
            "database no longer holds is here while its grants are still live, since a "
            "reload would revoke them and a restart is what removes the agent itself."
        )
    )


class PromptDiff(BaseModel):
    """The agents whose prompt text the stored configuration would move,
    which is the half of an agent entry a reload assembles again rather
    than the half a restart loads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    applies: Applies
    changed: tuple[str, ...] = Field(
        description=(
            "The agents whose stored `prompt` or `prompt_includes` differs from what "
            "this server is serving, sorted. A reload applies both, and each agent that "
            "moved assembles the new text at its next activation. Only agents both "
            "sides hold are compared: a reload keeps the agents this server started "
            "with, so an agent the database has added or deleted rides the added or "
            "removed lists above and nothing about its prompt is pending here. The "
            "fragments the includes name are their own kind, and an edit to a "
            "fragment's text is reported there rather than against every agent that "
            "carries it."
        )
    )


class AgentsDiff(EntityDiff):
    """The agents, whose entries span three regimes: an agent's `mcp`
    list is what a reload derives its tools from, its `prompt` and
    `prompt_includes` are what a reload assembles its next activation
    from, and everything else about the entry waits for a restart.

    So a grants-only or prompt-only edit is deliberately absent from
    `changed` above and reported under `grants` or `prompt` instead, and
    an answer that named it in both would be claiming a restart that
    nothing is waiting for.
    """

    grants: GrantsDiff
    prompt: PromptDiff


class SingletonDiff(BaseModel):
    """A kind there is exactly one of, which therefore has nothing to
    name: it moved or it did not.

    The `mcp` grants are left out of the comparison for the reason they
    are left out of an agent's, since this is the layer under every
    agent: they are what a reload applies, and what they moved is
    reported under `agents.grants`.
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
    not reported as pending.
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
# configuration this process booted on, the credentials loaded with it,
# and the registry whose world a reload replaces) is the composition
# root's business and not this API's. It answers the whole comparison,
# composed where the two worlds are, so the handler awaits it and adds
# nothing. None is the honest answer for an application without a
# server, and the route refuses rather than reporting an empty diff,
# which would say that nothing is pending.
type ConfigDiffReader = Callable[[], Awaitable[ConfigDiff]]


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
            "When the change takes effect, as one of a handful of sentences. Most of "
            "the configuration is read once at start, so most writes apply at the next "
            "server start. A device binding and the default agent are read by the "
            "running server, so they apply at the device's next OTA check or "
            "connection with no restart, unless they name an agent this server has not "
            "loaded, which is the case that carries the restart sentence again. A "
            "write to an MCP server entry, to one of its secret slots or to a prompt "
            "fragment names the reload instead, since that is what applies it to a "
            "running server. A write to an agent names both, its prompt fields and its "
            "`mcp` grants being applied by the reload and the rest of it built at the "
            "next start. A "
            "server serving a configuration no store describes says that the write is "
            "stored and takes effect when a server boots from that store."
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
