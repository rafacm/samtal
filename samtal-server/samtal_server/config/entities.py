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

A fact group is filled by the milestone that wires its consumer. Where
a group has no consumer yet it carries its default and says which
milestone fills it, so that nothing here is a value nothing validates.
The exceptions, filled now, are the static identity facts: how the kind
is addressed, whether it has a delete, whether stored secrets can hang
on it, and which moved configuration key its command is quoted for.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel

from samtal_server.config.models import (
    AgentConfig,
    AgentDefaults,
    FillerConfig,
    McpGrant,
    McpServerConfig,
    PromptFragmentConfig,
    ProviderConfig,
)

# Where the example fragments and the configuration file live, relative
# to the committed reference (docs/reference/domain-config.md). Printed
# as written when the same document goes to stdout.
EXAMPLES = "../../samtal-server/examples"
CONFIG_FILE = "../../samtal-server/config.example.yaml"

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
API_OPTIONS_NOTE = f"{_OPTIONS_CONTRACT} under `samtal-server/examples/`{_OPTIONS_WHERE}"

# A function a milestone hangs on a descriptor once it has a caller.
# Deliberately loose: each group's signature is settled beside the code
# that calls it, and naming one here would be a guess dressed up as a
# contract.
Hook = Callable[..., object] | None


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
    fields_in_help: ClassVar[bool] = False


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
    fields_in_help: ClassVar[bool] = True

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

    # Store facts (M2). The table the kind is rowed in, and the row
    # mapping: the default path is `model_validate`/`model_dump`, and a
    # hook is what a kind pays when its model demands one, which the
    # inventory proved for exactly three of the five. `before_parse` and
    # `inside_write` are the checks the three quirky kinds run around
    # their write.
    table: str | None = None
    from_row: Hook = None
    to_row: Hook = None
    before_parse: Hook = None
    inside_write: Hook = None

    # View facts (M2): the body builder that masks one entry for
    # display. `views.provider_record` is deliberately not this, and
    # stays hand-built for the reason its docstring gives.
    body: Hook = None

    # API facts (M3). Each endpoint's stable operation identity, exact
    # description, response and status declarations and parameter
    # signature: the committed OpenAPI document derives those bytes from
    # today's named handlers and their docstrings, so a route factory
    # has to install them rather than compose them. The element type is
    # M3's to settle beside that factory; the group is named here so the
    # milestone that needs it does not invent a second descriptor.
    endpoints: tuple[object, ...] = ()

    # The refusal for an entry that is not there (M3), used by both the
    # read and the delete. The fragments answer one fixed sentence that
    # does not repeat the name that was asked for; the others keep their
    # own sentences; the singleton has no missing case.
    missing: Hook = None

    # CLI facts (M4): the renderer that summarizes one entry in a
    # listing. The subparser's name is `name` and its arguments are
    # `addressing`, so there is nothing else for the grammar to carry.
    summary: Hook = None

    # Writes facts (M4): the acknowledgement sentences, and the notice
    # that says when the write applies.
    wrote: Hook = None
    deleted: Hook = None
    notice: Hook = None


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

    # API facts (M3), CLI facts (M4), writes facts (M4). A setting's
    # verbs are its own (bind, unbind, set, clear), so the groups are
    # named rather than shaped until the milestone that installs them.
    endpoints: tuple[object, ...] = ()
    summary: Hook = None
    wrote: Hook = None
    deleted: Hook = None
    notice: Hook = None


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
        command="samtal-server config set provider <stage> <name> -f fragment.yaml",
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
        secret_slots="provider",
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
        command="samtal-server config set mcp-server <name> -f fragment.yaml",
        examples=("mcp-server-stdio.yaml", "mcp-server-streamable-http.yaml"),
        route="/mcp-servers",
        addressing=("name",),
        moved_key="mcp_servers",
        secret_slots="mcp_server",
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
        command="samtal-server config set prompt-fragment <name> -f fragment.yaml",
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
            "There is no length cap. `samtal-server config prompt <agent>` reports what "
            "each block costs and what the whole prompt costs, which is what an "
            "operator tunes a small model's context budget against.",
        ),
        route="/prompt-fragments",
        addressing=("name",),
        moved_key="prompt_fragments",
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
        command="samtal-server config set agent <name> -f fragment.yaml",
        examples=("agent.yaml",),
        notes=(
            "An agent's name is also the key its remembered facts are stored under, "
            "so renaming an agent orphans its memory: the old file stays on disk and "
            f"the renamed agent starts empty. The `memory:` section in "
            f"[`config.example.yaml`]({CONFIG_FILE}) says what to do about it.",
        ),
        route="/agents",
        addressing=("name",),
        moved_key="agents",
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
        command="samtal-server config set agent-defaults -f fragment.yaml",
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
        has_delete=False,
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
            "`samtal-server config status` prints and what the model calls."
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
    ),
)


# The domain-level fields that are not entities of their own: a mapping
# and a scalar, written with their own commands rather than a fragment.
SETTINGS: tuple[Setting, ...] = (
    Setting(
        name="devices",
        title="Devices",
        command="samtal-server config bind-device <mac> <agent> [<agent> ...]",
        notes=(
            "A MAC is stored in its canonical form (lowercase, colon separated), so "
            "`AA-BB-CC-DD-EE-FF` and `aa:bb:cc:dd:ee:ff` are the same device.",
            "`samtal-server config delete device <mac>` removes a binding.",
        ),
        route="/devices",
        addressing=("mac",),
    ),
    Setting(
        name="default_agent",
        title="Default agent",
        command="samtal-server config set-default-agent <name>",
        notes=(
            "`samtal-server config clear-default-agent` unsets it, which is a "
            "configuration rather than a mistake: the devices map is then the "
            "allowlist.",
            "It is required only when agents are defined and no device is bound to "
            "one, and that rule is checked at boot rather than at write time, so a "
            "deployment can be built up in the natural order without wedging.",
        ),
        route="/default-agent",
    ),
)


__all__ = [
    "API_OPTIONS_NOTE",
    "CONFIG_FILE",
    "ENTITIES",
    "EXAMPLES",
    "NESTED",
    "OPTIONS_NOTE",
    "SETTINGS",
    "DocumentedShape",
    "EntityDescriptor",
    "Hook",
    "NestedShape",
    "Setting",
]
