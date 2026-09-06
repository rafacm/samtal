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

Imports the models, the wire vocabulary and the standard library, and
nothing else, deliberately: every consumer sits above this, so a
descriptor stays readable by the one that renders documentation on a
machine with no database, no encryption key and no FastAPI, which is
exactly what `docgen` is. `responses` is on that list because a notice
carries the boundaries it announces as the tokens the API publishes,
and it imports pydantic and nothing of this server, which is the
property `test_cli_import_weight.py` holds it to.

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
    PROGRAM,
    SERVER_PROGRAM,
    AgentConfig,
    AgentDefaults,
    FallbackConfig,
    FillerConfig,
    McpGrant,
    McpServerConfig,
    MemoryPolicy,
    PromptFragmentConfig,
    ProviderConfig,
    is_mcp_secret_key,
    is_secret_option,
    spoken_identity,
)
from vinga_server.config.provider_options import declared_options
from vinga_server.config.responses import Applies

# Where the example fragments and the configuration file live, relative
# to the committed reference (docs/reference/domain-config.md). Printed
# as written when the same document goes to stdout.
EXAMPLES = "../../vinga-server/examples"
CONFIG_FILE = "../../vinga-server/config.example.yaml"

# What schema generation describes, what it cannot, and where the
# remainder is described instead. A provider entry passes every key
# beyond the declared ones through to its implementation
# (`extra="allow"`), so a type that has not declared an option model yet
# is one no schema can enumerate.
#
# Which types are declared is read out of the declaration rather than
# written here, which is possible because that module weighs nothing:
# `provider_options` is pydantic and `models`, so the reference can
# render this sentence with no database, no key and no engine loaded.
# The list and the tables further down the page therefore cannot come to
# disagree about which types are typed (#88).
#
# Two renderings of one claim, differing only in how they point at the
# fragments: the reference lists them further down its own page, the
# OpenAPI document has no page to point down. Built from one string, so
# the two cannot come to say different things.
_DECLARED_TYPES = ", ".join(
    f"{stage} {type_name}" for stage, type_name, _ in declared_options()
)

_OPTIONS_CONTRACT = (
    "A provider entry carries whatever options its `type` takes. The types that "
    f"declare an option model, as stage and type, are: {_DECLARED_TYPES}. Their "
    "options are checked when the entry is written and refused by name, they are "
    f"printed by `{PROGRAM} schema provider <stage> <type>`, and the "
    "reference lists their fields under the provider section. Every other type has "
    "its options passed through rather than declared, so no schema can list those, "
    "and until the rest are typed (#88) they are documented in the example "
    "fragments"
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


@dataclass(frozen=True, kw_only=True)
class Notice:
    """When a write takes effect: the boundaries it is waiting at, and
    the sentence that says so.

    One structure rather than two, because the two always were one fact.
    The sentence is what a person reads and the tokens are what a
    program branches on, and anything that had only the sentence had to
    recover the boundary by looking for a phrase in it, which is a
    second encoding of this pairing held together by a substring search.

    `applies` is the vocabulary the comparison read already publishes,
    so what a write announces and what a diff announces are one closed
    set and a consumer learns it once. A tuple because a binding to an
    agent this server is not serving yet is waiting at two boundaries at
    the same time.
    """

    applies: tuple[Applies, ...]
    sentence: str


# When a write takes effect. Seven notices, because there are seven
# answers, and each is a fact of what was written rather than of the
# route or the command that wrote it: the descriptors below name one
# each, and the three write paths choose between them where the answer
# depends on something a kind cannot know (`api._binding_notice`, which
# asks whether the agent a live row names is being served and which of
# the two live rows was written, `api._rename_notice`, which asks what a
# rename's transaction moved, and `cli._secret_notice`, which asks which
# kind a credential hangs on).

# The whole of what a running server still reads once and never again,
# which is the file half: the port, the directories, the limits, the
# barge-in tuning. No kind this API writes is in that half any more, so
# no descriptor names this sentence; it stays because a write path
# needs a default that promises nothing, and because a setting that
# genuinely waits for a start should have one true sentence to be
# answered with when it gets a write route.
RESTART_NOTICE = Notice(
    applies=(Applies.RESTART,),
    sentence=(
        "This applies at the next server start: the configuration is read once at boot."
    ),
)

# The first exception: a running server reads device bindings and the
# default agent as a device asks for them, so binding a board is done
# with the board in front of you rather than at the next maintenance
# window.
BINDING_NOTICE = Notice(
    applies=(Applies.CHECK_IN,),
    sentence=(
        "This applies at the device's next OTA check or connection: a running server "
        "reads device bindings as it needs them, so no restart is needed."
    ),
)

# The second, and unlike the first it is asked for rather than noticed:
# a running server re-reads the stored configuration and installs it
# when it is asked to. Written on every kind of the domain half, which
# is what it has come to be true of (#191): the providers, the MCP
# servers, the shared fragments, the agents and the layer under them are
# one apply's business now, and the sentence that used to be written
# only on the kinds an install applied whole is written on all of them.
#
# One line, and that is a decision rather than an accident (#371). This
# is printed on every entry of every domain-half write, so a script that
# writes nine of them prints it nine times, and a sentence long enough
# to be worth reading once is a wall of text at that count. The three
# clocks a change converges at are still true and still published; they
# moved to `vinga apply --help` and to the domain-config reference,
# which is where somebody asking that question is already looking.
#
# And it names no command, which is the other half of what it is
# allowed to say (#386). What a write is waiting at is a fact of this
# server and travels beside the sentence as `applies`; which command
# crosses that boundary is a fact of a client's grammar, and a client
# is a program this one neither ships nor versions. So the sentence
# states, and a client that knows the set speaks in its own words
# instead, out of `cli.SPOKEN`; one that does not quotes this.
APPLY_NOTICE = Notice(
    applies=(Applies.RELOAD,),
    sentence=(
        "This is stored and not yet serving: the running server goes on serving what "
        "it already has until the stored configuration is installed on it."
    ),
)

# The third, for the binding whose agent this server is not serving
# yet. Both halves are true at once, which is why neither of the two
# above will do: the row itself is live, so the device meets it at its
# next check-in, and the agent it names arrives at the apply that
# installs it rather than at a restart, which is what an operator who
# has just written both would otherwise be sent away to do.
BINDING_UNSERVED_NOTICE = Notice(
    applies=(Applies.RELOAD, Applies.CHECK_IN),
    sentence=(
        "The binding applies at the device's next OTA check or connection, but this "
        "server is not serving the agent it names yet: the agent arrives with the "
        "install that adds it, and the device reaches it at the check-in after that."
    ),
)

# The fourth, for a server that was handed its configuration rather than
# reading one: the test lane's shape and an embedded caller's. Nothing
# this process serves reads the store these writes land in, so neither
# of the two live sentences above is true of them, and the one thing
# that is true is that the write is stored.
SNAPSHOT_NOTICE = Notice(
    applies=(Applies.STORE_BOOT,),
    sentence=(
        "This is stored and takes effect when a server starts from this store: the "
        "server answering this request serves a configuration it was given rather than "
        "one it read from a store, so nothing it is running reads what was just "
        "written."
    ),
)

# The fifth, for a rename that moved a device binding or the default
# agent with the agent it renamed. Two boundaries at once, exactly as
# the binding above, and for a different pair of reasons: the stored
# rows are live, so a device meets the moved reference at its next
# check-in, and the agent under its new name arrives at the install that
# applies the stored configuration.
#
# It cannot borrow `BINDING_UNSERVED_NOTICE`. That sentence is about one
# binding, written for the verb that writes one, and a rename may have
# moved several bindings and the default agent, none of which the
# operator just wrote. This one is about what a rename is, which is why
# it says the references rather than the binding.
#
# And it says a device that RESOLVES to the agent rather than one bound
# to it, which is the difference the two live rows make. The default
# agent covers the devices that have no binding of their own, so a
# rename that moved the default alone moved the reference of precisely
# the devices that are not bound to the agent, and a sentence naming a
# bound device would be false of every device it was about. One
# sentence for both arms, because a caller cannot act on the difference:
# what is waiting is the same install either way.
#
# And which arm a rename lands on is decided by what its transaction
# rewrote rather than by what the running server is serving, which is
# where it differs from the binding above. `_binding_notice` asks
# whether this server already serves the named agent, and for a bind
# that question is sound; for a rename it is not, because a server may
# be serving an agent under the new name and it will not be this one. A
# rename is precisely the act that moves a name onto a different body,
# so an "already serving" claim built on that lookup would be wrong
# exactly when it mattered.
RENAME_UNSERVED_NOTICE = Notice(
    applies=(Applies.RELOAD, Applies.CHECK_IN),
    sentence=(
        "The stored references moved with the agent, but this server is still serving "
        "it under the old name: the renamed agent arrives with the install that "
        "applies the stored configuration, and a device that resolves to it, by its "
        "own binding or by the default agent, reaches it at the check-in after that."
    ),
)

# The sixth, for the default agent naming an agent this server is not
# serving yet. Same two boundaries as the binding above and for the same
# pair of reasons: the stored row is live, so a device meets it at its
# next check-in, and the agent it names arrives at the install that adds
# it.
#
# It cannot borrow `BINDING_UNSERVED_NOTICE` either, and for a reason
# that reached an operator (#424): "The binding" names a row a
# `write_device` just wrote, and a document that set a default agent and
# bound no device contains no binding at all, so the sentence was about
# something the operator had never written. What this one says instead is
# what a default agent is, which is the fact the binding sentence has no
# room for: it covers every device that no binding of its own claims, so
# the devices it is true of are precisely the ones nobody named.
DEFAULT_AGENT_UNSERVED_NOTICE = Notice(
    applies=(Applies.RELOAD, Applies.CHECK_IN),
    sentence=(
        "The default agent covers every device that has no binding of its own, and "
        "this server is not serving the agent it names yet: the agent arrives with "
        "the install that adds it, and a device reaches it at the check-in after that."
    ),
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
    # snapshot can catch an operator with. Both halves of the answer,
    # because the sentence and the boundaries it announces are one fact
    # (`Notice` above).
    notice: Notice


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
        command=f"{PROGRAM} provider set <stage> <name> -f fragment.yaml",
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
        notice=APPLY_NOTICE,
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
        command=f"{PROGRAM} mcp-server set <name> -f fragment.yaml",
        examples=("mcp-server-stdio.yaml", "mcp-server-streamable-http.yaml"),
        route="/mcp-servers",
        addressing=("name",),
        moved_key="mcp_servers",
        table="mcp_servers",
        secret_slots="mcp_server",
        missing=NO_SUCH_MCP_SERVER,
        notice=APPLY_NOTICE,
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
        command=f"{PROGRAM} prompt-fragment set <name> -f fragment.yaml",
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
            f"There is no length cap. `{PROGRAM} agent preview <agent>` "
            f"reports what "
            "each block costs and what the whole prompt costs, which is what an "
            "operator tunes a small model's context budget against.",
        ),
        route="/prompt-fragments",
        addressing=("name",),
        moved_key="prompt_fragments",
        table="prompt_fragments",
        missing=NO_SUCH_FRAGMENT,
        notice=APPLY_NOTICE,
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
        command=f"{PROGRAM} agent set <name> -f fragment.yaml",
        examples=("agent.yaml",),
        notes=(
            "An agent's name is also the key its remembered facts, its held "
            "removals and its conversation threads are stored under, and renaming "
            "one moves all of them. A rename is a single transaction: the agents "
            "row, every device binding that names it, the default agent if it was "
            "one, the memory filed under it and the threads it owns, or nothing at "
            "all. What the device it is bound to knows is keyed by the board and is "
            "untouched either way.",
            "A rename is refused when the new name is already taken anywhere it "
            "would write: by an agent, by remembered facts, or by recorded "
            "conversation threads. That refusal is what keeps the act reversible, "
            "since a rename that merged two pasts into one could not be told apart "
            "afterwards by any second rename.",
            "What a rename deliberately does not rewrite is the record of what "
            "happened. A session, a turn and an event that was already written "
            "keeps the name the agent had when it was written, so a thread whose "
            "owner was renamed is filed under the new name with its earlier turns "
            "under the old one. A turn a conversation still in flight writes after "
            "the rename is a new write rather than an edit, and carries the name "
            "the agent has now; the session's own row keeps the name it opened "
            "with either way, because what that column records is the moment the "
            "session opened.",
            "The running server goes on serving the old name until the stored "
            "configuration is installed on it. What a conversation still in flight "
            "remembers in that window is filed under the old name, which is the one "
            "thing a rename leaves behind; "
            f"`{PROGRAM} memory list agent` is what shows such a row.",
            "Whether an agent remembers at all is its `memory` section, which is on "
            "unless it says otherwise. Switched off, the agent is offered no memory "
            "tools and is read no scope, its board's included.",
        ),
        route="/agents",
        addressing=("name",),
        moved_key="agents",
        table="agents",
        missing=NO_SUCH_AGENT,
        notice=APPLY_NOTICE,
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
        command=f"{PROGRAM} agent-defaults set -f fragment.yaml",
        examples=("agent-defaults.yaml",),
        notes=(
            "This entry is a singleton. There is one of it, writing it replaces it "
            "whole, and it is not keyed by anything. Per-family defaults are a later "
            "change, and re-keying the table is what it will do.",
            "An agent that names no provider for a stage inherits this entry's "
            "provider for that stage. A list field replaces rather than extends: an "
            "agent naming `mcp` names all of its MCP servers, and `mcp: []` opts it "
            "out of the tools its siblings have. The `filler`, `fallback` and "
            "`memory` sections behave the same way, each replacing this one wholly "
            "rather than merging with it.",
        ),
        route="/agent-defaults",
        addressing=(),
        moved_key="agent_defaults",
        table="agent_defaults",
        has_delete=False,
        notice=APPLY_NOTICE,
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
            f"`{PROGRAM} mcp-server status` prints and what the model calls."
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
            "voice ahead of time, at a start and again at every reload that moves "
            "them, and cached, so the clip costs nothing at the moment it "
            "masks and keeps working when the TTS provider is the thing being slow."
        ),
    ),
    NestedShape(
        name="fallback",
        title="Fallback",
        location="agent_defaults.fallback, agents.<name>.fallback",
        model=FallbackConfig,
        purpose=(
            "What a failed reply says out loud and on the display. Nested inside an "
            "agent or the agent defaults rather than written on its own, and on "
            "unless it says otherwise, which is the opposite default to the filler "
            "beside it: a turn that broke in silence is indistinguishable from a slow "
            "one. The phrase is synthesized in the agent's own voice ahead of time "
            "and cached, exactly as a filler phrase is, so a failed turn costs no "
            "text-to-speech call and says its piece even when the voice is what failed."
        ),
        notes=(
            "The phrase is fixed configuration and never the failure's own words. "
            "What reaches the arm that speaks this is whatever a provider or its "
            "transport raised, and a message from the far side of a network is the "
            "one thing about a broken turn that must not be read out loud.",
            "A phrase whose synthesis fails degrades rather than disappears: the "
            "failed turn still shows the sentence on the display and still closes "
            "with the `tts stop` a device waits on, and only the audio is lost. The "
            f"outcome is reported per agent by `{PROGRAM} apply`.",
        ),
    ),
    NestedShape(
        name="memory",
        title="Memory",
        location="agent_defaults.memory, agents.<name>.memory",
        model=MemoryPolicy,
        purpose=(
            "Whether an agent remembers anything at all. Nested inside an agent or "
            "the agent defaults rather than written on its own, and on unless it "
            "says otherwise. Off is the whole family at once: the agent is offered "
            "none of the memory tools and its prompt carries none of the scope "
            "blocks, so it can neither write what it is told nor read what it or "
            "its board was told before."
        ),
        notes=(
            "The device scope goes with the switch. An agent that may not remember "
            "is not read the notes its siblings on the same board accrued either, "
            "because an agent told what the room knows and unable to write any of "
            "it down is a half-off agent rather than an off one.",
            "Nothing stored is deleted by switching this off. The rows stay under "
            "the agent's name, the operator surface still shows them, and switching "
            "it back on is an agent that remembers what it remembered before; "
            f"`{PROGRAM} memory delete` is what takes rows away.",
        ),
    ),
)


# The domain-level fields that are not entities of their own: a mapping
# and a scalar, written with their own commands rather than a fragment.
SETTINGS: tuple[Setting, ...] = (
    Setting(
        name="devices",
        title="Devices",
        command=f"{PROGRAM} device bind <mac> <agent> [<agent> ...]",
        notes=(
            "A MAC is stored in its canonical form (lowercase, colon separated), so "
            "`AA-BB-CC-DD-EE-FF` and `aa:bb:cc:dd:ee:ff` are the same device.",
            f"`{PROGRAM} device delete <mac>` removes a binding.",
        ),
        route="/devices",
        addressing=("mac",),
        missing=NO_SUCH_DEVICE,
    ),
    Setting(
        name="default_agent",
        title="Default agent",
        command=f"{PROGRAM} default-agent set <name>",
        notes=(
            f"`{PROGRAM} default-agent clear` unsets it, which is a "
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

# Which kind holds a stored secret of each kind a stored location may
# name, read off the registry: `secret_slots` IS the word a location's
# `kind` carries, so the two vocabularies cannot come apart.
#
# Here rather than derived where it is needed, because it was derived
# three times: the store resolves a location to the row it hangs on, the
# CLI renders a location back into the command that fills it and reads
# the keys as the closed set a stored answer's `kind` is checked
# against, and a location now says itself through the addressing this
# module owns. Three derivations of one mapping are two pending bugs,
# and the shape of the bug is silent: a kind that gained secret slots
# would be admitted by whichever of them was updated.
SECRET_HOLDERS: dict[str, EntityDescriptor] = {
    entry.secret_slots: entry for entry in ENTITIES if entry.secret_slots is not None
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


# How an entity is addressed
#
# One fact read every way anything needs it, and it lives here because
# this module is where the addressing itself is declared: a kind's
# `addressing` tuple says which parameters name one of its entries, and
# these are that tuple joined, joined under its section, and read back
# apart again. Everything that has to agree about an address (the store,
# the API, the display, the CLI's export, and the build that names an
# entry it could not make) reaches one of them rather than spelling a
# join of its own.
#
# Here rather than beside the secrets, which is where `provider_identity`
# used to sit on the strength of its many readers. That is a reason to
# re-export it, which `secrets.py` does, and not a reason to leave the
# definition inside a module that imports cryptography: the CLI is one of
# the readers and it holds no key.


def provider_identity(stage: str, name: str) -> str:
    """A provider's identity: its stage and its name together, since two
    stages may hold the same name.

    One home for the string, because two callers have to agree about it:
    the location a stored secret is written under, and anything asking
    the store what it holds for that provider. A second spelling would
    ask about an entity nothing has ever written to, and the empty
    answer that comes back looks exactly like nothing stored.
    """
    return f"{stage}.{name}"


def entity_location(descriptor: EntityDescriptor, *identity: str) -> str:
    """Where an entry is written in the configuration document, which is
    what every refusal about it names: the section it lives in, and the
    parameters that address one entry under it.

    Through the door every SPOKEN identity goes through
    (`models.spoken_identity`), which costs a write nothing and is what
    a READ needs. A name reaches this on the write path only after the
    addressability check has passed it, and such a name holds neither
    the slash a URL credential brings nor a control character, so there
    is nothing left for that door to take. A name reaches it on the read
    path from the database, where a row written before those rules still
    sits: `agents.<name>: the row cannot be read` is a sentence composed
    over stored state and printed by a boot, and it was the one
    identity-speaking refusal with no strip on it (#382).

    Here rather than in the store, which is where it was written and
    where its only callers were, because the build names the same
    entries again after the composition has finished with them
    (`provider_label` below) and two spellings of one location is how a
    boot comes to say `providers.llm.x` about a row it refused and
    something else about the entry it could not build (#413).
    """
    said = (spoken_identity(part) for part in identity)
    return ".".join((descriptor.moved_key, *said))


def provider_label(stage: str, name: str) -> str:
    """What every refusal about one provider entry names it, and what
    the entry is called in the one event a build emits about it.

    The build's own name for a row the store already has a name for, so
    it is that name: `entity_location` above, given the kind, which is
    what keeps the sentence a boot prints about an unreadable provider
    row and the sentence it prints about an unbuildable one in one
    vocabulary.

    One home because the two halves of a build have to agree about it.
    The constructor composes it for its own refusals and for every
    factory and option reader below it, and the owner composes it again
    for the checks that only run once an object exists, since refusing
    one of those means closing what it refused. An entry refused by
    either half is the same entry (#413).
    """
    return entity_location(descriptor("provider"), stage, name)


def addressed(descriptor: EntityDescriptor, identity: str) -> tuple[str, ...]:
    """One entry's identity back as the parameters that address it.

    The inverse of the dotted join every surface names an entry by, and
    one home for it because three of them ask: an applied document,
    which names an entry by that join; a stored secret's location, whose
    identity is the same join; and the CLI's export, which renders a
    location back into the `secret set` command that fills it.

    Split at the first separator only, and only as many times as the
    kind has parameters, which is what keeps a name holding a dot still
    one name: `providers.llm.claude.v2` is the `claude.v2` of the `llm`
    stage, and nothing about a name forbids the dot. A kind addressed by
    nothing is the singleton, whose identity is the empty one.
    """
    if not descriptor.addressing:
        return ()
    return tuple(identity.split(".", len(descriptor.addressing) - 1))


__all__ = [
    "SERVER_PROGRAM",
    "API_OPTIONS_NOTE",
    "BINDING_NOTICE",
    "BINDING_UNSERVED_NOTICE",
    "DEFAULT_AGENT_UNSERVED_NOTICE",
    "CONFIG_FILE",
    "ENTITIES",
    "EXAMPLES",
    "NESTED",
    "NO_SUCH_AGENT",
    "NO_SUCH_DEVICE",
    "NO_SUCH_FRAGMENT",
    "NO_SUCH_MCP_SERVER",
    "NO_SUCH_PROVIDER",
    "OPTIONS_NOTE",
    "APPLY_NOTICE",
    "RESTART_NOTICE",
    "PROGRAM",
    "SECRET_HOLDERS",
    "SETTINGS",
    "SNAPSHOT_NOTICE",
    "DocumentedShape",
    "EntityDescriptor",
    "NestedShape",
    "Notice",
    "Setting",
    "addressed",
    "descriptor",
    "entity_location",
    "provider_identity",
    "provider_label",
    "setting",
]
