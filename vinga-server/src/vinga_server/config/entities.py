"""The domain entities as data: one descriptor per kind.

An entity kind is spread across this package. It is a pydantic model, a
table and a row mapping, a masked view, a pair of API routes, a CLI
subcommand with its acknowledgement, and a section of the generated
reference, and until this module existed each of those surfaces knew
the entity by hand. The models stay the source of field truth. This is
the one place for what a model cannot carry: how the kind is addressed,
which surfaces it has, and the prose that is about the kind rather than
about any field of it.

Three tiers, because the package really has three. Five kinds are
written with a command of their own and read back through the API
(`EntityDescriptor`). Two shapes are only ever nested inside one of
those five, and so have no command, no route and no example file of
their own (`NestedShape`). Two domain-level fields are a mapping and a
scalar rather than an entity, written with their own verbs and read
without an envelope (`Setting`). One registry tuple per tier, in the
order the reference documents them.

Imports the models and the standard library, and nothing else,
deliberately: every consumer sits above this, so a descriptor stays
readable by the one that renders documentation on a machine with no
database, no encryption key and no FastAPI, which is exactly what
`docgen` is.

Every fact is declared here, in the entry a reader meets the kind at,
and nothing is installed onto a descriptor afterwards. That is what the
frozen dataclass says and, since #210's fourth milestone, what it means:
there is no `fill`, no import-time mutation, and therefore no order the
modules above have to be imported in for a descriptor to be whole.

Which is possible because the facts are data or prose, and only ever
those: the three vocabularies a kind is addressed in (its route, its key
in the configuration document, its table), whether it has a delete,
whether stored secrets can hang on it, which of its keys carry a
credential, what a read or a delete of an entry that is not there
answers, and when a write of it takes effect. Behavior is not a fact
about a kind. A row mapping is written in terms of the repository's own
helpers, a masked body in terms of the masking rules, a route in terms
of FastAPI, and each of those lives with the code it is written in,
where its caller can see it and its signature can be honest.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel

from vinga_server.config.models import (
    AgentConfig,
    AgentDefaults,
    FillerConfig,
    McpGrant,
    McpServerConfig,
    PromptFragmentConfig,
    ProviderConfig,
    is_mcp_secret_key,
    is_secret_option,
)

# Where the example fragments and the configuration file live, relative
# to the committed reference (docs/reference/domain-config.md). Printed
# as written when the same document goes to stdout.
EXAMPLES = "../../vinga-server/examples"
CONFIG_FILE = "../../vinga-server/config.example.yaml"

# What schema generation cannot describe, and where it is described
# instead. A provider entry passes every key beyond the declared ones
# through to its implementation (`extra="allow"`), so no schema can
# enumerate them until typed option models land.
#
# Two renderings of one claim, differing only in how they point at the
# fragments: the reference lists them further down its own page, the
# OpenAPI document has no page to point down. Built from one string, so
# the two cannot come to say different things.
_OPTIONS_CONTRACT = (
    "A provider entry carries whatever options its `type` takes, and those are "
    "passed through rather than declared, so no schema can list them. Until typed "
    "option models land (#88) they are documented in the example fragments"
)
_OPTIONS_WHERE = ", which is also where the measured numbers behind each default are kept."
OPTIONS_NOTE = f"{_OPTIONS_CONTRACT} below{_OPTIONS_WHERE}"
API_OPTIONS_NOTE = f"{_OPTIONS_CONTRACT} under `vinga-server/examples/`{_OPTIONS_WHERE}"

# What a read or a delete of an entry that is not there says. One fixed
# sentence per kind, naming the section and the fact and never the
# identity that was asked for (#132).
#
# An identity that addresses nothing is a value nothing in this
# deployment has validated. It arrived in a URL path or on a command
# line, which is where a paste lands, and the sentence built from it
# travels out as a 404 body, as a printed line, and into whatever the
# caller's own log keeps. The section is what an operator needs to be
# told; the identity is the thing they typed and can see.
#
# Fixed for every kind, including the ones whose identity has a rigid
# shape. A MAC that reached a refusal is a MAC only in the sense that
# something parsed it that way, and a rule with an exception in it is a
# rule that gets the exception wrong later.
NO_SUCH_PROVIDER = "providers: no provider of that name exists for that stage"
NO_SUCH_MCP_SERVER = "mcp_servers: no MCP server of that name exists"
NO_SUCH_FRAGMENT = "prompt_fragments: no prompt fragment of that name exists"
NO_SUCH_AGENT = "agents: no agent of that name exists"
NO_SUCH_DEVICE = "devices: no device with that MAC is bound"


# When a write takes effect. Three sentences, because there are three
# answers, and each is a fact of what was written rather than of the
# route or the command that wrote it: the descriptors below name one
# each, `writes.py` chooses between them where the answer depends on
# something a kind cannot know, and both write paths print whichever
# came back.

# Printed after most mutating commands, and answered with most
# successful writes over HTTP. The configuration is a boot-time snapshot
# by design, and a write that quietly waits for a restart is the one
# thing about that design an operator can be caught by, so the write
# itself says when it takes effect.
RESTART_NOTICE = (
    "This applies at the next server start: the configuration is read once at boot."
)

# The first exception: a running server reads device bindings and the
# default agent as a device asks for them, so binding a board is done
# with the board in front of you rather than at the next maintenance
# window.
BINDING_NOTICE = (
    "This applies at the device's next OTA check or connection: a running server "
    "reads device bindings as it needs them, so no restart is needed."
)

# The second, and unlike the first it is asked for rather than noticed:
# a running server re-reads the MCP entries, the secrets stored on them
# and the agents' grant lists when the reload asks it to. Written on the
# writes that the reload actually applies and nowhere else, because a
# notice that is right about one field of a fragment and wrong about the
# rest is worse than the conservative sentence.
MCP_RELOAD_NOTICE = (
    "This applies when the running server is asked to reload: run "
    "`vinga-server config reload`, which re-reads the MCP servers and the agents' "
    "grant lists and applies them without a restart and without dropping a "
    "conversation."
)


@dataclass(frozen=True, kw_only=True)
class DocumentedShape:
    """What every shape in the generated reference carries: its model,
    and the prose no model can hold.

    The prose is about the kind. A field's description belongs on the
    field, where the three renderings read it from; what is here is what
    the kind is for, where it lives in the configuration document, and
    the notes that are true of the whole of it.
    """

    name: str
    title: str
    location: str
    model: type[BaseModel]
    purpose: str
    notes: tuple[str, ...] = ()

    # Which of the model's fields a display puts first, before the rest
    # in the order the model declares them.
    #
    # Almost always empty, because declaration order is display order: a
    # shape that listed its own fields here would be a second copy of
    # the model's list, which is the drift this registry exists to end.
    # What it is for is the one thing declaration order cannot say, that
    # a field declared last is read first. A subclass declares its own
    # fields after the ones it inherits, so an agent's prompt, which is
    # what makes it that agent, would arrive after the overrides that
    # qualify it.
    leads_with: tuple[str, ...] = ()

    # Which of the model's fields a display shows even when they hold
    # their declared default.
    #
    # Almost always empty, because the display rule already answers it:
    # a field is shown at whatever it holds, and the only thing left out
    # is a default that means absence (null, an empty list, an empty
    # mapping), so a default that is a real value is shown at it. This
    # is where a shape says it departs from that, and the departure has
    # to earn its line.
    always_shown: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class NestedShape(DocumentedShape):
    """A shape only ever written inside another entity.

    It is documented and it has a JSON Schema, and that is all: there is
    no command that writes one, no route that addresses one, and no
    example file of its own, because a reader meets it inside the entity
    that holds it. Those three are constants of the tier rather than
    per-shape data, which is what makes "nested" a tier and not a flag.
    """

    command: ClassVar[None] = None
    examples: ClassVar[tuple[str, ...]] = ()


@dataclass(frozen=True, kw_only=True)
class EntityDescriptor(DocumentedShape):
    """One entity kind that is written with a command of its own.

    Everything a surface needs to handle the kind generically, so that
    adding a field to its model is a change to the model and at most to
    this entry, rather than to the dozen sites the field would otherwise
    have to be spelled at.
    """

    # Documentation, read by `docgen`.
    #
    # The command that writes one, which is also the command the loader
    # quotes when it finds this kind still in the YAML file: one string,
    # rather than the two byte-identical copies the inventory found.
    command: str
    examples: tuple[str, ...] = ()

    # Addressing. The API path prefix, and the parameters that address
    # one entry under it, in order: they are the URL's path parameters
    # and the CLI's positional arguments, which are the same names for
    # the same reason. A provider takes two because a provider is
    # addressed by stage and name together, which is data here rather
    # than the special case it is at every hand-written site.
    route: str
    addressing: tuple[str, ...]

    # The key this kind occupies in the domain half of the configuration
    # document: a member of `models.DOMAIN_KEYS`, and the key the
    # loader's moved-section refusal looks `command` up under.
    moved_key: str

    # Whether the kind has a delete at all. The agent defaults are a
    # singleton: there is one of them, writing it replaces it, and there
    # is no route, no subparser and no sentence for deleting it. A fact
    # of the kind rather than a branch in five places.
    has_delete: bool = True

    # Which `secrets.EntityKind` member a stored secret on this kind is
    # addressed under, or None for a kind that can hold none. Held as a
    # plain string because this module imports the models and nothing
    # else; the two members are still exactly two.
    secret_slots: str | None = None

    # The table the kind is rowed in: addressing again, in the third of
    # the three vocabularies a kind is addressed in. Named rather than
    # held, because this module is read by a command with no database to
    # open, so resolving the name against the schema is the
    # repository's.
    table: str | None = None

    # Which of this kind's key names carry a credential, asked at every
    # depth of an entry the display path walks. The one field here whose
    # value is a function, and it earns it: this is a rule about names,
    # declared in the entry beside the kind it is true of, not behavior
    # a surface installs. One rule per kind rather than one per surface,
    # and it is the same predicate the models refuse an inline value
    # under, so what a write rejects and what a read masks cannot come
    # to disagree.
    #
    # The wider reading is the default, because a kind that has not
    # thought about the question should mask more rather than less: it
    # counts `auth`, since the key holding an MCP server's credential is
    # as often called Authorization as token. A provider takes the
    # narrower one, and that is the deliberate part: its options are
    # passed through to an implementation, where `auth_type: bearer` is
    # configuration an operator reads back rather than a credential.
    secret_key: Callable[[str], bool] = is_mcp_secret_key

    # The refusal for an entry that is not there, used by the read, the
    # delete and the slot check. One fixed sentence naming the section,
    # never built from what was addressed (see the constants above). A
    # string rather than anything computed at the request, because
    # nothing about the answer depends on the request any more; the
    # singleton has no missing case, and says so by carrying none.
    missing: str | None = None

    # When a write of this kind takes effect, which is one sentence per
    # kind rather than one per route: an MCP server and its stored
    # credentials are what a reload re-reads, and everything else waits
    # for the restart that reads the configuration again. Effect timing
    # is static and about the kind, so it is declared inline above like
    # every other fact. The two kinds whose notice does depend on what
    # was written (a device binding, whose agent may not be loaded) are
    # settings, and compute theirs at the call site as they always have.
    #
    # Required, because every commanded kind has an answer: a write that
    # could not say when it applies is the one thing the boot-time
    # snapshot can catch an operator with.
    notice: str


@dataclass(frozen=True, kw_only=True)
class Setting:
    """One domain-level field that is not an entity of its own.

    A mapping and a scalar, written with their own verbs rather than
    from a fragment and read without an envelope, so they are described
    rather than pretended into the fragment shape. The description is
    the model's: `name` is a member of `models.DOMAIN_KEYS`, and
    `DOMAIN_DESCRIPTIONS` is where its prose comes from.
    """

    name: str
    title: str
    # The command that writes it, quoted by the loader's moved-section
    # refusal under `name` for the same reason an entity's is.
    command: str
    notes: tuple[str, ...] = ()

    # Addressing, as for an entity: the API path, and the parameters
    # that address one entry under it.
    route: str
    addressing: tuple[str, ...] = ()

    # And the refusal for an entry that is not there, as for an entity
    # and for the same reason: a MAC that addresses no binding was typed
    # rather than stored. The scalar has no missing case (unset is a
    # configuration, not an absence), and says so by carrying none.
    missing: str | None = None

    # There is deliberately no `notice` here. A setting's routes are the
    # ones the entity tier cannot describe (bind by MAC, bind by
    # activation code, unbind, set, clear), its acknowledgement and its
    # notice are computed per request because a device binding's notice
    # depends on whether the server has the named agent loaded, and its
    # line in the summary tree is the agents a MAC points at rather than
    # a kind's entry. Declaring a fact nothing could answer would be an
    # invitation to force these two into a shape they are not.


ENTITIES: tuple[EntityDescriptor, ...] = (
    EntityDescriptor(
        name="provider",
        title="Provider",
        location="providers.<stage>.<name>",
        model=ProviderConfig,
        purpose=(
            "One engine, named so agents can reference it. Providers are grouped by "
            "the pipeline stage they serve (llm, asr, tts, vad), and an entry is "
            "addressed by that stage and its name together: two stages may hold the "
            "same name. A voice is a provider entry, so two agents that should sound "
            "different reference two entries."
        ),
        command="vinga-server config set provider <stage> <name> -f fragment.yaml",
        examples=(
            "llm-anthropic.yaml",
            "llm-openai-compatible.yaml",
            "asr-faster-whisper.yaml",
            "asr-openai.yaml",
            "tts-piper.yaml",
            "tts-elevenlabs.yaml",
            "tts-openai.yaml",
            "vad-silero.yaml",
        ),
        notes=(OPTIONS_NOTE,),
        route="/providers",
        addressing=("stage", "name"),
        moved_key="providers",
        table="providers",
        secret_slots="provider",
        secret_key=is_secret_option,
        missing=NO_SUCH_PROVIDER,
        notice=RESTART_NOTICE,
    ),
    EntityDescriptor(
        name="mcp-server",
        title="MCP server",
        location="mcp_servers.<name>",
        model=McpServerConfig,
        purpose=(
            "One MCP server, named so agents can reference it. The name becomes the "
            "prefix its tools are offered to the model under (`home__turn_on_light`), "
            "so it must match `[A-Za-z0-9_-]+` and must not be one of the names the "
            "merged tool list already uses. A server that is down at startup only "
            "logs a warning: its tools are absent, and it reconnects in the "
            "background when a session needs it."
        ),
        command="vinga-server config set mcp-server <name> -f fragment.yaml",
        examples=("mcp-server-stdio.yaml", "mcp-server-streamable-http.yaml"),
        route="/mcp-servers",
        addressing=("name",),
        moved_key="mcp_servers",
        table="mcp_servers",
        secret_slots="mcp_server",
        missing=NO_SUCH_MCP_SERVER,
        notice=MCP_RELOAD_NOTICE,
    ),
    EntityDescriptor(
        name="prompt-fragment",
        title="Prompt fragment",
        location="prompt_fragments.<name>",
        model=PromptFragmentConfig,
        purpose=(
            "One named block of prompt text, shared by the agents that include it. A "
            "fragment is written once and injected verbatim into the system prompt of "
            "every agent whose `prompt_includes` names it, which is how household "
            "facts or a house style stay in one place instead of being copied into "
            "every persona prompt and drifting apart. The name appears in the "
            "provenance the assembled prompt is reported under (`fragment:<name>`), so "
            "it must match `[A-Za-z0-9_-]+`."
        ),
        command="vinga-server config set prompt-fragment <name> -f fragment.yaml",
        examples=("prompt-fragment.yaml",),
        notes=(
            "Nothing is added around the text, not one heading: it is prompt text the "
            "operator wrote, and a heading would editorialize. The blocks are injected "
            "in the order the including layer lists them, after the agent's own prompt "
            "and before any MCP server's guidance, and the only bytes trimmed are "
            "whitespace at the two ends of the whole assembled prompt.",
            "A fragment that some layer still includes cannot be deleted, which is the "
            "same reference rule that keeps a referenced provider or MCP server from "
            "being taken away underneath an agent.",
            "There is no length cap. `vinga-server config prompt <agent>` reports what "
            "each block costs and what the whole prompt costs, which is what an "
            "operator tunes a small model's context budget against.",
        ),
        route="/prompt-fragments",
        addressing=("name",),
        moved_key="prompt_fragments",
        table="prompt_fragments",
        missing=NO_SUCH_FRAGMENT,
        notice=RESTART_NOTICE,
    ),
    EntityDescriptor(
        name="agent",
        title="Agent",
        location="agents.<name>",
        model=AgentConfig,
        purpose=(
            "One agent: a prompt, plus whichever stages it overrides. Every stage "
            "must resolve to a provider, on the agent or through agent_defaults, for "
            "the server to start, so a typical agent is a prompt and a voice."
        ),
        command="vinga-server config set agent <name> -f fragment.yaml",
        examples=("agent.yaml",),
        # The prompt is the agent, and the stages it overrides qualify
        # it, so a read opens with it. `AgentConfig` declares it after
        # the layer fields it inherits, which is an ordering of the
        # class rather than of the entry.
        leads_with=("prompt",),
        notes=(
            "An agent's name is also the key its remembered facts are stored under, "
            "so renaming an agent orphans its memory: the old file stays on disk and "
            f"the renamed agent starts empty. The `memory:` section in "
            f"[`config.example.yaml`]({CONFIG_FILE}) says what to do about it.",
        ),
        route="/agents",
        addressing=("name",),
        moved_key="agents",
        table="agents",
        missing=NO_SUCH_AGENT,
        notice=RESTART_NOTICE,
    ),
    EntityDescriptor(
        name="agent-defaults",
        title="Agent defaults",
        location="agent_defaults",
        model=AgentDefaults,
        purpose=(
            "What every agent uses unless it names something else. One entry for the "
            "whole deployment, and deliberately without a prompt: a prompt is what "
            "makes an agent that agent, and inheriting one silently would make two "
            "agents the same one."
        ),
        command="vinga-server config set agent-defaults -f fragment.yaml",
        examples=("agent-defaults.yaml",),
        notes=(
            "This entry is a singleton. There is one of it, writing it replaces it "
            "whole, and it is not keyed by anything. Per-family defaults are a later "
            "change, and re-keying the table is what it will do.",
            "An agent that names no provider for a stage inherits this entry's "
            "provider for that stage. A list field replaces rather than extends: an "
            "agent naming `mcp` names all of its MCP servers, and `mcp: []` opts it "
            "out of the tools its siblings have. A `filler` section behaves the same "
            "way, replacing this one wholly rather than merging with it.",
        ),
        route="/agent-defaults",
        addressing=(),
        moved_key="agent_defaults",
        table="agent_defaults",
        has_delete=False,
        notice=RESTART_NOTICE,
    ),
)


NESTED: tuple[NestedShape, ...] = (
    NestedShape(
        name="mcp-grant",
        title="MCP grant",
        location="agent_defaults.mcp[], agents.<name>.mcp[]",
        model=McpGrant,
        purpose=(
            "One entry of an `mcp` list, in the form that grants part of a server "
            "rather than all of it. An entry written as a plain string is the whole "
            "server; an entry written as this object is the tools it lists and "
            "nothing else, so an agent can switch the lights without being able to "
            "unlock the door. Tools are named by the published name without the "
            "entry prefix (`turn_on_light` for `home__turn_on_light`), which is what "
            "`vinga-server config status` prints and what the model calls."
        ),
        notes=(
            "There is no deny list, deliberately. A denied set fails open: a server "
            "that adds a tool would silently grant it to every agent that denied the "
            "old ones, which is exactly wrong on the shared family device this "
            "exists for.",
            "A name that matches nothing cannot be refused when it is written, since "
            "only a live connection knows what a server publishes. It is logged when "
            "the server publishes its tools, and the status surface shows the allow "
            "list beside the published list, so the mismatch is answerable in one "
            "read.",
        ),
    ),
    NestedShape(
        name="filler",
        title="Filler",
        location="agent_defaults.filler, agents.<name>.filler",
        model=FillerConfig,
        purpose=(
            "Masking reply latency with a pre-synthesized filled pause. Nested inside "
            "an agent or the agent defaults rather than written on its own, and off "
            "unless it says otherwise. The phrases are synthesized in the agent's own "
            "voice at boot and cached, so the clip costs nothing at the moment it "
            "masks and keeps working when the TTS provider is the thing being slow."
        ),
        # The phrase list is what the section is: an entry with none is
        # a filler that plays nothing, which is a state to read off the
        # section rather than to infer from a key that is not there. The
        # empty list is also unreachable while the feature is on, since
        # the model refuses `enabled` without phrases, so what this
        # shows is the disabled entry as it stands.
        always_shown=("phrases",),
    ),
)


# The domain-level fields that are not entities of their own: a mapping
# and a scalar, written with their own commands rather than a fragment.
SETTINGS: tuple[Setting, ...] = (
    Setting(
        name="devices",
        title="Devices",
        command="vinga-server config bind-device <mac> <agent> [<agent> ...]",
        notes=(
            "A MAC is stored in its canonical form (lowercase, colon separated), so "
            "`AA-BB-CC-DD-EE-FF` and `aa:bb:cc:dd:ee:ff` are the same device.",
            "`vinga-server config delete device <mac>` removes a binding.",
        ),
        route="/devices",
        addressing=("mac",),
        missing=NO_SUCH_DEVICE,
    ),
    Setting(
        name="default_agent",
        title="Default agent",
        command="vinga-server config set-default-agent <name>",
        notes=(
            "`vinga-server config clear-default-agent` unsets it, which is a "
            "configuration rather than a mistake: the devices map is then the "
            "allowlist.",
            "It is required only when agents are defined and no device is bound to "
            "one, and that rule is checked at boot rather than at write time, so a "
            "deployment can be built up in the natural order without wedging.",
        ),
        route="/default-agent",
    ),
)


_BY_NAME: dict[str, EntityDescriptor] = {entry.name: entry for entry in ENTITIES}

_SETTINGS_BY_NAME: dict[str, Setting] = {entry.name: entry for entry in SETTINGS}

_BY_MODEL: dict[type[BaseModel], DocumentedShape] = {
    shape.model: shape for shape in (*ENTITIES, *NESTED)
}


def descriptor(name: str) -> EntityDescriptor:
    """One commanded kind, by the name its command, its route and its
    documentation section all carry."""
    return _BY_NAME[name]


def setting(name: str) -> Setting:
    """One domain-level field, by its key in the configuration document.

    The other tier's accessor, so a fact that is true of both tiers
    (what a miss answers with, so far) is read the same way for either,
    rather than being a constant here for one of them and a descriptor
    fact for the rest.
    """
    return _SETTINGS_BY_NAME[name]


def leads_with(model: type[BaseModel]) -> tuple[str, ...]:
    """Which of one model's fields a display puts before the rest.

    Addressed by the model, and answering with nothing for one the
    registry does not carry, for the reasons `always_shown` below gives:
    these are the two questions a display asks about a shape that the
    shape's own field list cannot answer, and they are asked in the same
    place and the same way.
    """
    shape = _BY_MODEL.get(model)
    return shape.leads_with if shape is not None else ()


def always_shown(model: type[BaseModel]) -> tuple[str, ...]:
    """Which of one model's fields a display shows even when they hold
    their declared default.

    Addressed by the model rather than by a kind's name, because this is
    what the display path asks as it walks into a section nested inside
    an entry, where the name it came in under is the field's and not a
    kind's. A model the registry does not carry answers with nothing,
    which is the rule rather than an absence of one: the display rule
    decides what a field shows, and a shape says only where it departs.
    """
    shape = _BY_MODEL.get(model)
    return shape.always_shown if shape is not None else ()


__all__ = [
    "API_OPTIONS_NOTE",
    "BINDING_NOTICE",
    "CONFIG_FILE",
    "ENTITIES",
    "EXAMPLES",
    "MCP_RELOAD_NOTICE",
    "NESTED",
    "NO_SUCH_AGENT",
    "NO_SUCH_DEVICE",
    "NO_SUCH_FRAGMENT",
    "NO_SUCH_MCP_SERVER",
    "NO_SUCH_PROVIDER",
    "OPTIONS_NOTE",
    "RESTART_NOTICE",
    "SETTINGS",
    "DocumentedShape",
    "EntityDescriptor",
    "NestedShape",
    "Setting",
    "always_shown",
    "descriptor",
    "leads_with",
    "setting",
]
