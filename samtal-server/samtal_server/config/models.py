"""Pydantic models for the samtal-server YAML configuration.

Top-level keys: server, providers, agent_defaults, agents, devices,
default_agent. Secrets are referenced by environment variable name (for
example api_key_env), never written inline.
"""

import re
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated

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

_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")

# Fragments that mark a provider option as secret-bearing. Keys ending in
# _env are the sanctioned pattern: they name an environment variable instead
# of holding the value.
_SECRET_KEY_FRAGMENTS = ("secret", "token", "password", "api_key", "apikey", "credential")

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
    agent_defaults: AgentDefaults = Field(default_factory=AgentDefaults)
    agents: dict[NonBlankStr, AgentConfig] = Field(default_factory=dict)
    devices: dict[str, NonBlankStr] = Field(default_factory=dict)
    default_agent: NonBlankStr | None = None

    @field_validator("devices", mode="before")
    @classmethod
    def _normalize_device_macs(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized: dict[str, str] = {}
        for mac, agent in value.items():
            key = normalize_mac(str(mac))
            if key in normalized:
                raise ValueError(f'device "{mac}" appears more than once (as {key})')
            normalized[key] = agent
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

        for mac, agent in self.devices.items():
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

    def agent_for_device(self, mac: str) -> str | None:
        """Resolve the agent name bound to a device MAC, falling back to
        default_agent for unknown devices."""
        return self.devices.get(normalize_mac(mac), self.default_agent)
