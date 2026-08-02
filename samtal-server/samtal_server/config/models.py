"""Pydantic models for the samtal-server YAML configuration.

Top-level keys: server, providers, mcp_servers, agent_defaults, agents,
devices, default_agent, memory. Secrets are referenced by environment
variable name (for example api_key_env, or a $VAR value in an MCP
server's env and headers), never written inline.
"""

import os
import re
from collections.abc import Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from samtal_server.tools import names

_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")

# Fragments that mark a provider option as secret-bearing. Keys ending in
# _env are the sanctioned pattern: they name an environment variable instead
# of holding the value.
_SECRET_KEY_FRAGMENTS = ("secret", "token", "password", "api_key", "apikey", "credential")

# The same rule for an MCP server's env and headers, where the key that
# carries a secret is as often called Authorization as it is token.
_MCP_SECRET_KEY_FRAGMENTS = (*_SECRET_KEY_FRAGMENTS, "auth")

# An environment reference in an MCP server's env or headers: the whole
# value is $NAME, which is resolved from the server's own environment at
# boot. A value that must begin with a literal $ is not supported.
_ENV_REFERENCE_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")

PROVIDER_STAGES = ("llm", "asr", "tts", "vad")

# Identifiers (provider names, agent names, references between them) must
# survive stripping with at least one character.
NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=8003, ge=1, le=65535)

    # The websocket URL handed to devices by the OTA endpoint. Left unset it
    # is derived from the address the device reached the OTA endpoint on,
    # which is right for a plain LAN deployment; set it explicitly when the
    # server sits behind a proxy or a name the request headers do not carry.
    websocket_url: str | None = None

    # Binary protocol version advertised to devices. The firmware defaults to
    # 1 (bare Opus frames); 2 and 3 add timestamp headers.
    protocol_version: int = Field(default=1, ge=1, le=3)

    # Minutes east of UTC, sent so the device can set its clock to local time.
    # Left unset the server's own current offset is used.
    timezone_offset_minutes: int | None = Field(default=None, ge=-1440, le=1440)

    @field_validator("websocket_url")
    @classmethod
    def _check_websocket_scheme(cls, value: str | None) -> str | None:
        if value is None:
            return value
        url = value.strip()
        if not url.startswith(("ws://", "wss://")):
            raise ValueError(
                f'"{value}" is not a websocket URL; it must start with ws:// or wss://'
            )
        return url


class ProviderConfig(BaseModel):
    """One provider entry. Options beyond `type` are passed through to the
    provider implementation, so extra keys are allowed here."""

    model_config = ConfigDict(extra="allow")

    type: NonBlankStr
    api_key_env: str | None = None

    @model_validator(mode="after")
    def _reject_inline_secrets(self) -> "ProviderConfig":
        for key in self.model_extra or {}:
            lowered = key.lower()
            if lowered.endswith("_env"):
                continue
            if any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS):
                raise ValueError(
                    f'"{key}" looks like an inline secret, which is not allowed; '
                    f"reference an environment variable instead, for example "
                    f"{key}_env: MY_PROVIDER_{key.upper()}"
                )
        return self

    @property
    def options(self) -> dict[str, object]:
        """Provider-specific options (everything beyond the declared fields)."""
        return dict(self.model_extra or {})


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: dict[NonBlankStr, ProviderConfig] = Field(default_factory=dict)
    asr: dict[NonBlankStr, ProviderConfig] = Field(default_factory=dict)
    tts: dict[NonBlankStr, ProviderConfig] = Field(default_factory=dict)
    vad: dict[NonBlankStr, ProviderConfig] = Field(default_factory=dict)


def _env_reference(value: str) -> str | None:
    """The variable name behind a `$VAR` value, or None for a literal."""
    match = _ENV_REFERENCE_RE.match(value.strip())
    return match.group(1) if match else None


def resolve_env_references(location: str, values: Mapping[str, str]) -> dict[str, str]:
    """A `$VAR` mapping with its secrets read from the server's own
    environment. Literal values for non-secret keys pass through. An
    unset variable raises, naming where it was written, because at call
    time it would fail every conversation that reaches the server.

    Kept out of the model so the parsed configuration never holds a
    secret: resolution happens at boot, where the value is used."""
    resolved: dict[str, str] = {}
    for key, value in values.items():
        name = _env_reference(value)
        if name is None:
            resolved[key] = value
            continue
        secret = os.environ.get(name, "")
        if not secret:
            raise ValueError(
                f"{location}.{key}: references ${name}, but it is not set in the environment"
            )
        resolved[key] = secret
    return resolved


class McpServerConfig(BaseModel):
    """One MCP server, named so agents can reference it.

    `transport` decides which of the two field groups applies: a stdio
    server is a command this server spawns, a streamable_http one is a
    URL it connects to. Naming a field of the other transport is an
    error rather than a silently ignored key, since the difference
    between "my headers are ignored" and "my headers are wrong" is a
    debugging afternoon.
    """

    model_config = ConfigDict(extra="forbid")

    transport: Literal["stdio", "streamable_http"]

    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    # How long one tool call on this server may take before the model is
    # told it timed out. Spoken silence is the cost, so it is short.
    tool_timeout_s: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def _check_transport_fields(self) -> "McpServerConfig":
        stdio_only = ("command", "args", "env")
        http_only = ("url", "headers")
        if self.transport == "stdio":
            required, foreign = "command", http_only
        else:
            required, foreign = "url", stdio_only

        problems: list[str] = []
        value = getattr(self, required)
        if value is None or not str(value).strip():
            problems.append(f'transport "{self.transport}" needs "{required}"')
        named = [field for field in foreign if field in self.model_fields_set]
        if named:
            problems.append(
                f'transport "{self.transport}" has no {", ".join(named)}; '
                f"that belongs to the other transport"
            )
        problems += self._secret_problems()
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def _secret_problems(self) -> list[str]:
        """Secret-bearing env and header keys must name an environment
        variable, the same rule that keeps provider secrets out of the
        configuration file."""
        problems: list[str] = []
        for group, values in (("env", self.env), ("headers", self.headers)):
            for key, value in values.items():
                lowered = key.lower()
                if not any(fragment in lowered for fragment in _MCP_SECRET_KEY_FRAGMENTS):
                    continue
                if _env_reference(value) is None:
                    problems.append(
                        f'{group}.{key} looks like an inline secret, which is not '
                        f"allowed; reference an environment variable instead, for "
                        f"example {key}: $MY_SERVER_SECRET"
                    )
        return problems


class MemoryConfig(BaseModel):
    """Where the agents' remembered facts are kept.

    The whole section is optional: without it there is no `remember`
    tool and nothing is injected into any prompt.
    """

    model_config = ConfigDict(extra="forbid")

    dir: Path


class AgentDefaults(BaseModel):
    """Provider references every agent inherits unless it names its own.

    Deliberately no prompt: a persona's prompt is its identity, and
    inheriting one silently would make two agents the same agent.
    """

    model_config = ConfigDict(extra="forbid")

    llm: NonBlankStr | None = None
    asr: NonBlankStr | None = None
    tts: NonBlankStr | None = None
    vad: NonBlankStr | None = None

    # The MCP servers this agent talks to. None means inherit; a list
    # replaces rather than extends the inherited one, like the stage
    # fields, so an agent naming an empty list opts out of tools.
    mcp: list[NonBlankStr] | None = None


class AgentConfig(AgentDefaults):
    """One persona: a prompt, plus whichever stages it overrides."""

    prompt: str = ""


def normalize_mac(value: str) -> str:
    """Normalize a MAC address to lowercase colon-separated form."""
    mac = value.strip().lower().replace("-", ":")
    if not _MAC_RE.match(mac):
        raise ValueError(
            f'"{value}" is not a MAC address; expected six colon-separated '
            f"hex pairs, for example aa:bb:cc:dd:ee:ff"
        )
    return mac


# The YAML file the settings source should read, set by the loader around
# instantiation. pydantic-settings has no init kwarg for a runtime-chosen
# path yet (pydantic-settings#259).
yaml_file_var: ContextVar[Path | None] = ContextVar("samtal_yaml_file", default=None)


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        extra="forbid",
        env_prefix="SAMTAL_",
        env_nested_delimiter="__",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file_var.get()),
            file_secret_settings,
        )

    server: ServerConfig = Field(default_factory=ServerConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    # Named like providers, and referenced by agents the same way. The
    # entry name becomes the prefix its tools are offered under.
    mcp_servers: dict[NonBlankStr, McpServerConfig] = Field(default_factory=dict)
    memory: MemoryConfig | None = None
    agent_defaults: AgentDefaults = Field(default_factory=AgentDefaults)
    agents: dict[NonBlankStr, AgentConfig] = Field(default_factory=dict)
    # One device may be bound to several agents; the value is written as a
    # single name or a list, and always stored as a list.
    devices: dict[str, list[NonBlankStr]] = Field(default_factory=dict)
    default_agent: NonBlankStr | None = None

    @field_validator("mcp_servers")
    @classmethod
    def _check_entry_names(
        cls, value: dict[str, McpServerConfig]
    ) -> dict[str, McpServerConfig]:
        """An entry name becomes a tool-name prefix, so it has to be a
        legal tool name, and it may not be one the merged list already
        uses. That is what makes a namespace collision unrepresentable
        rather than something to resolve at merge time."""
        problems = [
            f'mcp_servers.{name}: not a usable entry name; it becomes a tool-name '
            f"prefix, so it must match [A-Za-z0-9_-]+ and must not be one of: "
            + ", ".join(names.RESERVED_ENTRY_NAMES)
            for name in value
            if not names.is_valid_entry_name(name)
        ]
        if problems:
            raise ValueError("\n".join(problems))
        return value

    @field_validator("devices", mode="before")
    @classmethod
    def _normalize_device_bindings(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized: dict[str, object] = {}
        for mac, bound in value.items():
            key = normalize_mac(str(mac))
            if key in normalized:
                raise ValueError(f'device "{mac}" appears more than once (as {key})')
            normalized[key] = _binding_as_list(key, bound)
        return normalized

    @model_validator(mode="after")
    def _check_references(self) -> "Config":
        problems: list[str] = []

        if self.agents and self.default_agent is None:
            problems.append(
                "default_agent is required when agents are defined; set it to one of: "
                + ", ".join(sorted(self.agents))
            )
        if self.default_agent is not None and self.default_agent not in self.agents:
            problems.append(
                f'default_agent "{self.default_agent}" is not a defined agent'
                + (f" (defined: {', '.join(sorted(self.agents))})" if self.agents else "")
            )

        for mac, bound in self.devices.items():
            for agent in bound:
                if agent not in self.agents:
                    problems.append(f'devices.{mac}: agent "{agent}" is not a defined agent')

        # Each layer's own references are checked where they are written,
        # so a wrong default is reported once as agent_defaults.llm rather
        # than once per agent that inherits it.
        sources: list[tuple[str, AgentDefaults]] = [("agent_defaults", self.agent_defaults)]
        sources += [(f"agents.{name}", agent) for name, agent in self.agents.items()]
        for source, layer in sources:
            for stage in PROVIDER_STAGES:
                ref = getattr(layer, stage)
                if ref is None:
                    continue
                available = getattr(self.providers, stage)
                if ref not in available:
                    hint = (
                        f" (defined: {', '.join(sorted(available))})"
                        if available
                        else f"; no providers.{stage} entries are defined"
                    )
                    problems.append(
                        f'{source}.{stage}: unknown {stage} provider "{ref}"{hint}'
                    )
            for entry in layer.mcp or []:
                if entry not in self.mcp_servers:
                    hint = (
                        f" (defined: {', '.join(sorted(self.mcp_servers))})"
                        if self.mcp_servers
                        else "; no mcp_servers entries are defined"
                    )
                    problems.append(f'{source}.mcp: unknown MCP server "{entry}"{hint}')

        if problems:
            raise ValueError("\n".join(problems))
        return self

    def provider_for_agent(self, agent: str, stage: str) -> tuple[str | None, str]:
        """The provider an agent uses for one stage, and the configuration
        location it came from: the agent's own entry when it names one,
        agent_defaults otherwise. The location is what error messages quote,
        so a mistake points at the layer that holds it."""
        own = getattr(self.agents[agent], stage)
        if own is not None:
            return own, f"agents.{agent}.{stage}"
        return getattr(self.agent_defaults, stage), f"agent_defaults.{stage}"

    def mcp_for_agent(self, agent: str) -> list[str]:
        """The MCP servers an agent talks to: its own list when it names
        one, agent_defaults otherwise. A list replaces rather than
        extends, so `mcp: []` is how an agent opts out of tools its
        siblings have."""
        own = self.agents[agent].mcp
        if own is not None:
            return list(own)
        return list(self.agent_defaults.mcp or [])

    def referenced_mcp_servers(self) -> set[str]:
        """The entries some agent actually uses. Only these are
        connected at startup, the way only referenced providers are
        built."""
        return {entry for agent in self.agents for entry in self.mcp_for_agent(agent)}

    def agents_for_device(self, mac: str) -> list[str]:
        """The agents a device may talk to, the first of them the one a
        conversation starts on. Unknown devices fall back to default_agent;
        a device with no binding and no default_agent resolves to nothing,
        and is turned away."""
        bound = self.devices.get(normalize_mac(mac))
        if bound:
            return list(bound)
        return [self.default_agent] if self.default_agent is not None else []


def _binding_as_list(mac: str, bound: object) -> object:
    """A device binding written as one agent name or as a list, normalized
    to a list. Anything else is left for pydantic to report."""
    names = [bound] if isinstance(bound, str) else bound
    if not isinstance(names, list):
        return bound
    if not names:
        raise ValueError(f"devices.{mac}: bind the device to at least one agent")
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str):
            continue
        if name.strip() in seen:
            raise ValueError(f'devices.{mac}: agent "{name.strip()}" is listed more than once')
        seen.add(name.strip())
    return names
