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

from samtal_server.config.models import (
    PROVIDER_STAGES,
    AgentConfig,
    AgentDefaults,
    McpServerConfig,
    ProviderConfig,
    is_mcp_secret_key,
    is_secret_option,
)
from samtal_server.config.secrets import mask
from samtal_server.config.store import Entity, Snapshot, StoredSecret, stored_secrets

# Entity envelopes


def provider(read: Entity[ProviderConfig]) -> dict[str, object]:
    return _envelope(provider_body(read.entry), read.secrets)


def mcp_server(read: Entity[McpServerConfig]) -> dict[str, object]:
    return _envelope(mcp_server_body(read.entry), read.secrets)


def agent(read: Entity[AgentConfig]) -> dict[str, object]:
    return _envelope(agent_body(read.entry), read.secrets)


def agent_defaults(read: Entity[AgentDefaults]) -> dict[str, object]:
    return _envelope(layer_body(read.entry), read.secrets)


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
                stage: {
                    name: provider_body(entry)
                    for name, entry in sorted(getattr(domain.providers, stage).items())
                }
                for stage in PROVIDER_STAGES
            },
            "mcp_servers": {
                name: mcp_server_body(entry)
                for name, entry in sorted(domain.mcp_servers.items())
            },
            "agent_defaults": layer_body(domain.agent_defaults),
            "agents": {
                name: agent_body(entry) for name, entry in sorted(domain.agents.items())
            },
            "devices": {mac: list(bound) for mac, bound in sorted(domain.devices.items())},
            "default_agent": domain.default_agent,
        },
        "secrets": [_stored(secret) for secret in stored_secrets(snapshot)],
    }


def providers(snapshot: Snapshot) -> dict[str, dict[str, object]]:
    """Every provider, by stage and then by name: the way a provider is
    addressed everywhere else, since two stages may hold one name."""
    stored = _by_entity(snapshot)
    return {
        stage: {
            name: _envelope(provider_body(entry), stored.get(("provider", f"{stage}.{name}"), ()))
            for name, entry in sorted(getattr(snapshot.domain.providers, stage).items())
        }
        for stage in PROVIDER_STAGES
    }


def mcp_servers(snapshot: Snapshot) -> dict[str, object]:
    stored = _by_entity(snapshot)
    return {
        name: _envelope(mcp_server_body(entry), stored.get(("mcp_server", name), ()))
        for name, entry in sorted(snapshot.domain.mcp_servers.items())
    }


def agents(snapshot: Snapshot) -> dict[str, object]:
    return {
        name: _envelope(agent_body(entry), ())
        for name, entry in sorted(snapshot.domain.agents.items())
    }


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
    return data


def agent_body(entry: AgentConfig) -> dict[str, object]:
    return {"prompt": entry.prompt, **layer_body(entry)}


def layer_body(entry: AgentDefaults) -> dict[str, object]:
    """The override half an agent and the agent defaults share."""
    data: dict[str, object] = {
        stage: getattr(entry, stage)
        for stage in PROVIDER_STAGES
        if getattr(entry, stage) is not None
    }
    if entry.mcp is not None:
        data["mcp"] = list(entry.mcp)
    if entry.filler is not None:
        data["filler"] = entry.filler.model_dump()
    return data


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
    "layer_body",
    "masked_option",
    "mcp_server",
    "mcp_server_body",
    "mcp_servers",
    "provider",
    "provider_body",
    "providers",
    "reference_value",
    "shown_values",
]
