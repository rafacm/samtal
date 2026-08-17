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
two secret-shaped-name predicates on the models, and this module is the
one place that rule is applied to an entity. It fails closed: a value in
a secret-bearing key that is not a syntactically valid environment
reference is masked, whatever it is, because a value that got in another
way may be a plaintext credential and the command an operator runs to
find that mistake must not be the one that prints it.
"""

from collections.abc import Mapping, Sequence

from samtal_server.config import entities
from samtal_server.config.models import (
    PROVIDER_STAGES,
    AgentConfig,
    AgentDefaults,
    McpServerConfig,
    PromptFragmentConfig,
    ProviderConfig,
    is_mcp_secret_key,
    is_secret_option,
    mcp_entry_fragment,
    without_url_credential,
)
from samtal_server.config.secrets import mask
from samtal_server.config.store import Entity, Snapshot, StoredSecret, stored_secrets

# Entity envelopes
#
# Which builder shows one entry is a fact about the kind, so it is the
# kind's descriptor that says it and these are the names its callers
# know it by.


def entity(kind: str, read: Entity[object]) -> dict[str, object]:
    """One entity as a read shows it: its kind's masked body, inside the
    envelope every kind is read through."""
    return _envelope(entities.descriptor(kind).body(read.entry), read.secrets)


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
            "agent_defaults": entities.descriptor("agent-defaults").body(domain.agent_defaults),
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
        name: _envelope(descriptor.body(entry), stored.get((descriptor.secret_slots, name), ()))
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
                descriptor.body(entry),
                stored.get((descriptor.secret_slots, f"{stage}.{name}"), ()),
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


def provider_body(entry: ProviderConfig) -> dict[str, object]:
    """One provider as it may be displayed. Whatever a secret-shaped key
    holds goes through the mask, which passes an environment reference
    through as itself and fails closed on everything else: nothing
    validates the shape of an api_key_env value, so an operator who
    pasted the key where its variable name belongs must not have it read
    back out by the command they would run to find the mistake."""
    data: dict[str, object] = {"type": entry.type}
    if entry.api_key_env is not None:
        data["api_key_env"] = mask(entry.api_key_env)
    if entry.egress is not None:
        data["egress"] = entry.egress
    data.update(
        {
            key: mask(value) if is_secret_option(key) else masked_option(value)
            for key, value in entry.options.items()
        }
    )
    return data


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

    Built key by key rather than by dumping the model, so a field added
    to `ProviderConfig` later is absent from every record until somebody
    decides it belongs there.
    """
    data: dict[str, object] = {"type": entry.type}
    if entry.api_key_env is not None:
        data["api_key_env"] = mask(entry.api_key_env)
    if entry.egress is not None:
        data["egress"] = entry.egress
    data.update(
        {
            key: mask(value) if is_secret_option(key) else recorded_option(value)
            for key, value in entry.options.items()
        }
    )
    return data


def recorded_option(value: object) -> object:
    """One provider option as it may be recorded, at every depth: what
    was configured, minus anything a URL carries in front of its host or
    in a credential-shaped parameter."""
    if isinstance(value, Mapping):
        return {
            key: mask(nested) if is_secret_option(key) else recorded_option(nested)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [recorded_option(item) for item in value]
    if isinstance(value, str):
        return without_url_credential(value)
    return value


def masked_option(value: object) -> object:
    """One provider option, masked at every depth.

    An option can be a structure, because options are passed through to
    the provider implementation, so a secret-shaped key can be nested
    inside one. The models refuse to accept such a key now, but the
    display path does not rely on that: it is the last thing standing
    between a row that got its contents another way and a caller, so it
    fails closed on its own. A secret-shaped key masks whatever it
    holds, structures included.
    """
    if isinstance(value, Mapping):
        return {
            key: mask(nested) if is_secret_option(key) else masked_option(nested)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [masked_option(item) for item in value]
    return value


def mcp_server_body(entry: McpServerConfig) -> dict[str, object]:
    data: dict[str, object] = {"transport": entry.transport}
    if entry.command is not None:
        data["command"] = entry.command
    if entry.args:
        data["args"] = list(entry.args)
    if entry.env:
        data["env"] = shown_values(entry.env)
    if entry.url is not None:
        data["url"] = entry.url
    if entry.headers:
        data["headers"] = shown_values(entry.headers)
    if entry.egress is not None:
        data["egress"] = entry.egress
    data["tool_timeout_s"] = entry.tool_timeout_s
    # Shown as written, and unmasked: it is guidance the operator wrote
    # for the model to read, not a credential slot.
    if entry.instructions is not None:
        data["instructions"] = entry.instructions
    # Always shown, like the timeout beside it: what a read of an entry
    # answers about a trust decision should be the decision, and "off"
    # is one of its two states rather than the absence of one.
    data["use_server_instructions"] = entry.use_server_instructions
    # Shown only when the operator named prompts, so an unset list reads
    # as the "none" it is rather than as an emptied one. The names are
    # the server's, and this is where operator-written configuration is
    # echoed write-shaped, which is the one place they may appear.
    if entry.inject_prompts is not None:
        data["inject_prompts"] = list(entry.inject_prompts)
    return data


def prompt_fragment_body(entry: PromptFragmentConfig) -> dict[str, object]:
    """One fragment as it may be displayed, which is as it was written:
    it is prompt text for the model to read, and there is nothing in it
    to mask."""
    return {"text": entry.text}


def agent_body(entry: AgentConfig) -> dict[str, object]:
    return {"prompt": entry.prompt, **layer_body(entry)}


def layer_body(entry: AgentDefaults) -> dict[str, object]:
    """The override half an agent and the agent defaults share."""
    data: dict[str, object] = {
        stage: getattr(entry, stage)
        for stage in PROVIDER_STAGES
        if getattr(entry, stage) is not None
    }
    # Each entry in the form it was written in, so a read is a fragment
    # a write of it accepts back: a plain name stays a name, and a grant
    # stays {server, tools} rather than becoming one of them.
    if entry.mcp is not None:
        data["mcp"] = [mcp_entry_fragment(item) for item in entry.mcp]
    if entry.filler is not None:
        data["filler"] = entry.filler.model_dump()
    # Shown only when the layer wrote one, so an unset list reads as the
    # inherit it is rather than as an empty one, which means the
    # opposite.
    if entry.prompt_includes is not None:
        data["prompt_includes"] = list(entry.prompt_includes)
    return data


# Which builder shows one entry of each kind. `provider_record` is
# deliberately not among them: a record is not a display, and what it
# leaves out and why is its own docstring's.
entities.fill("provider", body=provider_body)
entities.fill("mcp-server", body=mcp_server_body)
entities.fill("prompt-fragment", body=prompt_fragment_body)
entities.fill("agent", body=agent_body)
entities.fill("agent-defaults", body=layer_body)


def device_body(agents: Sequence[str]) -> dict[str, object]:
    """A binding is a list of agent names, in the shape a write of one
    takes, so what a read shows is what a write accepts back."""
    return {"agents": list(agents)}


def shown_values(values: Mapping[str, str]) -> dict[str, object]:
    """An MCP server's env or headers as they may be displayed. The
    model already requires a $VAR for the secret-bearing keys, so this
    changes nothing for a valid entry; it is what stops a value that got
    in another way from being read back out."""
    return {
        key: mask(value) if is_mcp_secret_key(key) else value for key, value in values.items()
    }


def reference_value(body: Mapping[str, object], key: str) -> object:
    """What an entity writes under one of its reference-carrying keys,
    addressed the way a stored secret addresses it: a dotted key reaches
    into an MCP server's env or headers, a bare one is a provider's own
    key. Masked already, because the body it reads is."""
    group, dotted, name = key.partition(".")
    if not dotted:
        return body.get(key)
    nested = body.get(group)
    return nested.get(name) if isinstance(nested, Mapping) else None


def _bodies(section: Mapping[str, object], kind: str) -> dict[str, object]:
    """Every entry of one kind as the document shows it, by name: the
    bare bodies, since the document says where the stored secrets are in
    a list of its own."""
    body = entities.descriptor(kind).body
    return {name: body(entry) for name, entry in sorted(section.items())}


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
    "agent_body",
    "agent_defaults",
    "agents",
    "config",
    "default_agent",
    "device",
    "device_body",
    "devices",
    "entity",
    "layer_body",
    "listing",
    "masked_option",
    "mcp_server",
    "mcp_server_body",
    "mcp_servers",
    "prompt_fragment",
    "prompt_fragment_body",
    "prompt_fragments",
    "provider",
    "provider_body",
    "providers",
    "reference_value",
    "shown_values",
]
