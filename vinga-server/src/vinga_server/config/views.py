"""One masked view of the configuration, rendered two ways.

What a read shows is presentation, so it lives beside the callers rather
than in the repository: the API returns these dictionaries as JSON, and
the CLI renders the same ones as YAML with its comment lines underneath.
Two renderings of one view, so a mask that held in one and not the other
is not a thing that can happen.

The view is an envelope, never the bare entity. The entity's
model-shaped half is what a write accepts back, and by design it can
never carry ciphertext, so a bare entity would lose the one fact a
masked read exists to convey: which slots hold a stored secret, and what
each of them displaces. The CLI says that in comment lines; JSON has no
comments, so here it is structure:

    {"entity": {...}, "secrets": {"api_key": {"shadows": "api_key_env"}}}

Nothing here decides anything. Existence and precedence are the
repository's (`store.py`), the masking rule is `secrets.mask` plus the
secret-shaped-name predicate each kind's descriptor names, and this
module is the one place that rule is applied to an entity. It fails
closed: a value in a secret-bearing key that is not a syntactically
valid environment reference is masked, whatever it is, because a value
that got in another way may be a plaintext credential and the command an
operator runs to find that mistake must not be the one that prints it.

There are two rules, and the second one looks at what a string is
rather than at what it is called. A URL is the shape that carries a
credential under a key admitting to nothing, so what a display shows of
one is the address without it, whatever key it sits under and at
whatever depth (#381). It is asked of both halves of a pair: a mapping
keyed by whatever the caller wrote is the one place a rule about values
does not reach, so a key is stripped exactly as a value is (#408).
Both write paths refuse such a URL, and both refuse only at write time,
so this is what stands between a row that predates the rule and a
caller. `_shown` is where a value meets it and `_shown_mapping` is
where a key does; each says why it is the one site for its half.

What is displayed fails open, which is the other half of the same
decision (#176). A body is derived from the entry's model rather than
written key by key, so a field added to a model appears in `config show`
and in the whole-configuration document with nothing here to edit, and
appears masked if its name is secret-shaped. The alternative was
measured: a scratch field added during the descriptor work reached the
store, both APIs, the CLI and both generated references without anybody
touching them, and was invisible on every read, with no test failing for
it. Fail-open display and fail-closed masking are not in tension: the
walk that finds the new field is the walk that masks it, at every depth,
which is also how a nested credential inside an MCP server's env or
headers came to be masked (#171).

The record path is the opposite decision, deliberately, and
`provider_record` below says why.
"""

from collections.abc import Callable, Mapping, Sequence

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from vinga_server.config import entities
from vinga_server.config.models import (
    PROVIDER_STAGES,
    AgentConfig,
    AgentDefaults,
    FillerConfig,
    McpServerConfig,
    PromptFragmentConfig,
    ProviderConfig,
    is_secret_option,
    without_url_credential,
)
from vinga_server.config.secrets import mask, provider_identity
from vinga_server.config.store import Entity, Snapshot, StoredSecret, stored_secrets

# Entity envelopes
#
# One builder shows an entry of any kind (`entity_body` below), given
# the kind's descriptor. These are the names its callers know it by,
# one per kind, so a caller that holds a provider says so.


def entity(kind: str, read: Entity[object]) -> dict[str, object]:
    """One entity as a read shows it: its kind's masked body, inside the
    envelope every kind is read through."""
    return _envelope(_body(kind, read.entry), read.secrets)


def provider(read: Entity[ProviderConfig]) -> dict[str, object]:
    return entity("provider", read)


def mcp_server(read: Entity[McpServerConfig]) -> dict[str, object]:
    return entity("mcp-server", read)


def prompt_fragment(read: Entity[PromptFragmentConfig]) -> dict[str, object]:
    return entity("prompt-fragment", read)


def agent(read: Entity[AgentConfig]) -> dict[str, object]:
    return entity("agent", read)


def agent_defaults(read: Entity[AgentDefaults]) -> dict[str, object]:
    return entity("agent-defaults", read)


def device(read: Entity[list[str]]) -> dict[str, object]:
    return _envelope(device_body(read.entry), read.secrets)


def default_agent(name: str | None) -> dict[str, object]:
    """Not an envelope: the default agent is a name, not an entity, and
    it holds nothing that could be a secret."""
    return {"name": name}


# The whole configuration, and the identity-keyed listings


def config(snapshot: Snapshot) -> dict[str, object]:
    """The whole domain configuration, masked, with the location of
    every stored secret beside it.

    The document half is the shape the YAML file had, which is what
    `config show` prints and what a reader of the reference already
    knows. The secrets half is a list rather than a mapping because a
    location is three fields, not a key.
    """
    domain = snapshot.domain
    return {
        "config": {
            "providers": {
                stage: _bodies(getattr(domain.providers, stage), "provider")
                for stage in PROVIDER_STAGES
            },
            "mcp_servers": _bodies(domain.mcp_servers, "mcp-server"),
            "prompt_fragments": _bodies(domain.prompt_fragments, "prompt-fragment"),
            "agent_defaults": _body("agent-defaults", domain.agent_defaults),
            "agents": _bodies(domain.agents, "agent"),
            "devices": {mac: list(bound) for mac, bound in sorted(domain.devices.items())},
            "default_agent": domain.default_agent,
        },
        "secrets": [_stored(secret) for secret in stored_secrets(snapshot)],
    }


def listing(kind: str, snapshot: Snapshot) -> dict[str, object]:
    """Every entry of one kind, by name, each in its envelope. What is
    stored beside an entry comes from the same traversal for all of
    them, and a kind that can hold no stored secret answers with an
    empty mapping rather than with a different shape."""
    descriptor = entities.descriptor(kind)
    stored = _by_entity(snapshot)
    return {
        name: _envelope(
            entity_body(descriptor, entry),
            stored.get((descriptor.secret_slots, name), ()),
        )
        for name, entry in sorted(getattr(snapshot.domain, descriptor.moved_key).items())
    }


def providers(snapshot: Snapshot) -> dict[str, dict[str, object]]:
    """Every provider, by stage and then by name: the way a provider is
    addressed everywhere else, since two stages may hold one name."""
    descriptor = entities.descriptor("provider")
    stored = _by_entity(snapshot)
    return {
        stage: {
            name: _envelope(
                entity_body(descriptor, entry),
                stored.get((descriptor.secret_slots, provider_identity(stage, name)), ()),
            )
            for name, entry in sorted(getattr(snapshot.domain.providers, stage).items())
        }
        for stage in PROVIDER_STAGES
    }


def mcp_servers(snapshot: Snapshot) -> dict[str, object]:
    return listing("mcp-server", snapshot)


def prompt_fragments(snapshot: Snapshot) -> dict[str, object]:
    return listing("prompt-fragment", snapshot)


def agents(snapshot: Snapshot) -> dict[str, object]:
    return listing("agent", snapshot)


def devices(snapshot: Snapshot) -> dict[str, object]:
    return {
        mac: _envelope(device_body(bound), ())
        for mac, bound in sorted(snapshot.domain.devices.items())
    }


# Entity bodies, as they may be displayed


def entity_body(descriptor: entities.EntityDescriptor, entry: object) -> dict[str, object]:
    """One entry of one kind as it may be displayed: every field its
    model declares, masked at every depth by the kind's own
    secret-shaped-name rule, and every string in it shown without what a
    URL of it carries as a credential.

    One builder for the five kinds, specialized by the descriptor, in
    place of the five that were written key by key. Which fields exist
    is the model's to say and the registry's to point at, and a builder
    that repeated the list was a second copy of it: the copy could only
    ever fall behind, silently, since nothing reads a body to check that
    it is whole.

    Whatever a secret-shaped key holds goes through the mask, which
    passes an environment reference through as itself and fails closed
    on everything else: nothing validates the shape of an api_key_env
    value, so an operator who pasted the key where its variable name
    belongs must not have it read back out by the command they would run
    to find the mistake.
    """
    return _declared(entry, descriptor.secret_key)


def _declared(entry: object, secret_key: Callable[[str], bool]) -> dict[str, object]:
    """Every field a model declares, in the order a reader meets them,
    and then whatever a pass-through model was given beyond them (a
    provider's options, which are the implementation's and so cannot be
    declared).

    Declaration order is the display order, and this module is where
    the two shapes that depart from the display rules say so. Order is
    not presentation here: JSON and YAML both keep the order a mapping
    was built in, so this is the order an operator reads an entry in and
    the order the committed bytes of a response have.
    """
    model = type(entry)
    # A field is shown at whatever it holds, and the only thing left out
    # is a default that means absence (null, an empty list, an empty
    # mapping), so a default that is a real value is shown at it. The
    # filler section is the one departure: its phrase list is what the
    # section is, so an entry with none is a filler that plays nothing,
    # which is a state to read off the section rather than to infer from
    # a key that is not there. The empty list is unreachable while the
    # feature is on, since `FillerConfig` refuses `enabled` without
    # phrases, so what this shows is the disabled entry as it stands.
    shown = ("phrases",) if model is FillerConfig else ()
    data: dict[str, object] = {}
    for name in _order(model):
        field = model.model_fields[name]
        value = getattr(entry, name)
        if name in shown or not _absent(field, value):
            data[name] = _masked(name, value, secret_key)
    return _shown_mapping(
        data,
        getattr(entry, "model_extra", None) or {},
        lambda name, value: _masked(name, value, secret_key),
    )


def _order(model: type[BaseModel]) -> list[str]:
    """The field names of one model, in the order a display shows them:
    whatever the shape leads with, and then the rest as the model
    declares them.

    Declaration order is display order, with one departure, which is the
    one thing declaration order cannot say: that a field declared last
    is read first. An agent is its prompt, and the stages it overrides
    qualify it, so a read of one opens with the prompt; `AgentConfig`
    declares it after the layer fields it inherits, which is an ordering
    of the class rather than of the entry.
    """
    lead = ("prompt",) if model is AgentConfig else ()
    return [*lead, *(name for name in model.model_fields if name not in lead)]


def _masked(name: str, value: object, secret_key: Callable[[str], bool]) -> object:
    """One key and what it holds, as they may be displayed.

    A secret-shaped name displaces whatever it holds, structures
    included, and anything else is walked for the secret-shaped names
    inside it. The models refuse such a name below the top level now,
    and this does not rely on that: it is the last thing standing
    between a row that got its contents another way and a caller, so it
    fails closed on its own.
    """
    return mask(value) if secret_key(name) else _shown(value, secret_key)


def _shown(value: object, secret_key: Callable[[str], bool]) -> object:
    """One displayed value, walked to the bottom.

    Depth is the point, and it is why this is one walk rather than one
    per field group. A provider option can be a structure, because
    options are passed through to the provider implementation; a section
    nested in an agent is a model of its own; and an MCP server's env
    and headers are mappings whose values were once masked only at the
    top (#171). All three are the same question asked at a different
    depth, and a walk that stops anywhere answers it wrong there.

    The bottom is where the second rule is applied: a displayed string
    is shown without what a URL carries in front of its host or in a
    credential-shaped parameter (#381). Both write paths refuse such a
    URL now, an MCP server's `url` and a provider's `base_url` alike,
    but write time is all they are, and their own docstrings say a row
    written before the rule still boots and still reads. It does, and
    until this it read back verbatim: everything a caller is shown is
    built through `entity_body` or `_body`, so a single read, a listing,
    the whole-configuration document, the API routes over them and the
    CLI renderings over those were one leak with five spellings. Here
    rather than at each of them because this is where both kinds and
    every depth already meet, and because a rule applied field by field
    would be a list of field names to keep in step with the models.

    Display fidelity is not what it costs: `url_credential` answers None
    to anything that is not a URL actually carrying one, so every other
    string, prose holding an address included, is shown byte for byte.
    What it does change is an outcome rather than a rendering, and for
    the better. An export is this document in the shape an import takes,
    and an import runs the write path, so a store holding such a row
    used to export a document nothing could take, its own store
    included.
    """
    if isinstance(value, BaseModel):
        return _declared(value, secret_key)
    if isinstance(value, Mapping):
        return _shown_mapping(
            {}, value, lambda key, nested: _masked(key, nested, secret_key)
        )
    if isinstance(value, (list, tuple)):
        return [_shown(item, secret_key) for item in value]
    if isinstance(value, str):
        return without_url_credential(value)
    return value


def _shown_mapping(
    into: dict[str, object],
    mapping: Mapping[object, object],
    rendered: Callable[[object, object], object],
) -> dict[str, object]:
    """One mapping as it may leave this module: every key shown without
    what a URL of it carries, every value rendered by the caller's own
    rule, and no pair dropped.

    A key is as good a place to paste a credential as a value is, which
    is the write path's own words for why it refuses one there
    (`store._check_no_url_credentials`), and a mapping keyed by whatever
    the caller wrote is the one place a value's rule does not reach:
    a provider's options are `extra="allow"` at the top and pass-through
    structures below it, and an MCP server's `env` and `headers` are
    keyed by names somebody else chose. So the same strip is applied to
    both halves of a pair, and it is applied here, once, for every
    mapping any display or record builds (#408).

    Rendered from the key as it is STORED, never from the shown one.
    The two rules read the same name and only one of them may change it:
    an MCP env key spelled `https://host/x?auth=...` is secret-shaped by
    the wider fragment set, so its value is masked, and sanitizing the
    key first would take the word `auth` out of it and quietly stop the
    masking. Order is the whole of what keeps fail-closed masking and
    this strip from fighting.

    Two keys can sanitize to one spelling (`https://a:1@host/x` and
    `https://b:2@host/x` are both `https://host/x`), and a mapping
    comprehension would answer with the last of them and drop the rest.
    Dropping is not available here: a read is a fragment a write of it
    accepts back, so a pair silently missing from a read is a pair an
    operator deletes by re-importing what they were shown. A spelling
    already taken therefore gets `#2`, then `#3`, in the order the
    mapping holds its keys, which is the order the stored body has, so
    one row renders one way every time. The later key is the one that
    moves, whichever of the two was sanitized, because reserving the
    untouched keys first would mean two passes and a display order that
    is no longer the row's own. It costs nothing in practice: no write
    accepts such a key any more, so a collision needs a stored row
    holding two of them.
    """
    for key, value in mapping.items():
        display = without_url_credential(key) if isinstance(key, str) else key
        if display in into:
            display = _unclaimed(display, into)
        into[display] = rendered(key, value)
    return into


def _unclaimed(display: object, taken: Mapping[object, object]) -> str:
    """The first spelling of a shown key that nothing has claimed."""
    index = 2
    while f"{display}#{index}" in taken:
        index += 1
    return f"{display}#{index}"


# The defaults that mean a field holds nothing rather than something.
_ABSENCE = (None, [], {})


def _absent(field: FieldInfo, value: object) -> bool:
    """Whether a field is unwritten rather than written.

    A field is shown at whatever it holds, its default included: what a
    read answers about a decision should be the decision, and
    `use_server_instructions: false` is one of its two states rather
    than the absence of one. The exception is a default that means
    absence, which is what a null, an empty list or an empty mapping
    declared as the default says. Leaving those out is not decoration:
    a read is a fragment a write of it accepts back, and an MCP server
    is refused for naming a field of the other transport, so a stdio
    entry that showed `url: null` and `headers: {}` could not be written
    back at all.

    A list or a mapping the operator wrote is not that absence, and is
    shown: `prompt_includes: []` opts a layer out where an unset one
    inherits, and the two must not read alike.
    """
    if field.is_required():
        return False
    default = field.get_default(call_default_factory=True)
    return any(default is absence or default == absence for absence in _ABSENCE) and (
        value is default or value == default
    )


def provider_record(entry: ProviderConfig) -> dict[str, object]:
    """One provider as it may be *recorded*: written into a capture's
    manifest and into a conversation's session row, kept for as long as
    either is kept.

    A record is not a display, so the values stay as written: the exact
    model string is the only handle on a hosted model whose behaviour
    changed without a version bump, which is why the manifest carries
    the entries verbatim in the first place. What it is not allowed to
    carry is a credential, and there are two ways one reaches an entry.
    A secret-shaped key is masked, at every depth, the same rule the
    display path applies and for the same fail-closed reason. And a URL
    holding a user and password, or a credential in a query parameter,
    is recorded without it: the write path refuses such a URL now, but a
    row written before that rule, or an environment override that never
    passed through a write at all, must not be able to put one in a file
    that outlives the conversation.

    Built key by key rather than derived from the model, which is the
    half of the split policy that stays fail-closed. A display shows
    every field the model declares, because a read that hides one is an
    operator debugging with an incomplete answer, and a read is thrown
    away as soon as it has been read. A record is kept: it is written
    into a manifest and a session row that outlive the conversation, so
    a field added to `ProviderConfig` later is absent from every record
    until somebody decides it belongs there. Same question, opposite
    answers, because the cost of being wrong points the other way.
    """
    data: dict[str, object] = {"type": entry.type}
    if entry.api_key_env is not None:
        data["api_key_env"] = mask(entry.api_key_env)
    if entry.egress is not None:
        data["egress"] = entry.egress
    return _shown_mapping(data, entry.options, _recorded_pair)


def recorded_option(value: object) -> object:
    """One provider option as it may be recorded, at every depth: what
    was configured, minus anything a URL carries in front of its host or
    in a credential-shaped parameter, on either half of a pair.

    The key is stripped by the same rule and at the same site the
    display strips one (`_shown_mapping`), because it is the same rule:
    a manifest keyed by what the caller wrote would otherwise outlive
    the conversation carrying the credential the value no longer has
    (#408).
    """
    if isinstance(value, Mapping):
        return _shown_mapping({}, value, _recorded_pair)
    if isinstance(value, list):
        return [recorded_option(item) for item in value]
    if isinstance(value, str):
        return without_url_credential(value)
    return value


def _recorded_pair(key: object, value: object) -> object:
    """What one option holds as a record shows it, given the key as it
    is stored: the mask where the name admits to being a secret, and the
    option's own walk otherwise."""
    return mask(value) if is_secret_option(str(key)) else recorded_option(value)


def device_body(agents: Sequence[str]) -> dict[str, object]:
    """A binding is a list of agent names, in the shape a write of one
    takes, so what a read shows is what a write accepts back."""
    return {"agents": list(agents)}


def _body(kind: str, entry: object) -> dict[str, object]:
    """One entry as its kind is shown: the one builder above, given the
    kind's descriptor.

    One builder for all five, because what differs between them is the
    descriptor it is given rather than the way an entry is shown.
    `provider_record` is deliberately not reached through here: a record
    is not a display, and what it leaves out and why is its own
    docstring's.
    """
    return entity_body(entities.descriptor(kind), entry)


def _bodies(section: Mapping[str, object], kind: str) -> dict[str, object]:
    """Every entry of one kind as the document shows it, by name: the
    bare bodies, since the document says where the stored secrets are in
    a list of its own."""
    return {name: _body(kind, entry) for name, entry in sorted(section.items())}


def _envelope(
    body: dict[str, object], secrets: Sequence[StoredSecret]
) -> dict[str, object]:
    """The entity, plus what is stored beside it. Kinds that can hold no
    stored secret answer with an empty mapping rather than with a
    different shape, so one reader renders every read."""
    return {
        "entity": body,
        "secrets": {secret.location.slot: {"shadows": secret.shadows} for secret in secrets},
    }


def _stored(secret: StoredSecret) -> dict[str, object]:
    return {
        "kind": secret.location.kind,
        "identity": secret.location.identity,
        "slot": secret.location.slot,
        "shadows": secret.shadows,
    }


def _by_entity(snapshot: Snapshot) -> dict[tuple[str, str], list[StoredSecret]]:
    """The snapshot's stored secrets grouped by the entity holding them,
    which is what a listing needs and what one traversal gives it."""
    grouped: dict[tuple[str, str], list[StoredSecret]] = {}
    for secret in stored_secrets(snapshot):
        grouped.setdefault((secret.location.kind, secret.location.identity), []).append(secret)
    return grouped


__all__ = [
    "agent",
    "agent_defaults",
    "agents",
    "config",
    "default_agent",
    "device",
    "device_body",
    "devices",
    "entity",
    "entity_body",
    "listing",
    "mcp_server",
    "mcp_servers",
    "prompt_fragment",
    "prompt_fragments",
    "provider",
    "provider_record",
    "providers",
    "recorded_option",
]
