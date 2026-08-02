"""Pydantic models for the samtal-server YAML configuration.

Top-level keys: server, providers, agents, devices, default_agent. Secrets are
referenced by environment variable name (for example api_key_env), never
written inline.
"""

import re
from contextvars import ContextVar
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")

# Provider option names that would put a secret in the config file. Each has a
# *_env counterpart that names an environment variable instead.
_INLINE_SECRET_KEYS = frozenset(
    {"api_key", "apikey", "token", "access_token", "secret", "password"}
)

PROVIDER_STAGES = ("llm", "asr", "tts", "vad")


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=8003, ge=1, le=65535)


class ProviderConfig(BaseModel):
    """One provider entry. Options beyond `type` are passed through to the
    provider implementation, so extra keys are allowed here."""

    model_config = ConfigDict(extra="allow")

    type: str
    api_key_env: str | None = None

    @model_validator(mode="after")
    def _reject_inline_secrets(self) -> "ProviderConfig":
        for key in self.model_extra or {}:
            if key.lower() in _INLINE_SECRET_KEYS:
                raise ValueError(
                    f'inline secret "{key}" is not allowed; reference an environment '
                    f'variable instead, for example {key}_env: MY_PROVIDER_{key.upper()}'
                )
        return self

    @property
    def options(self) -> dict[str, object]:
        """Provider-specific options (everything beyond the declared fields)."""
        return dict(self.model_extra or {})


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: dict[str, ProviderConfig] = Field(default_factory=dict)
    asr: dict[str, ProviderConfig] = Field(default_factory=dict)
    tts: dict[str, ProviderConfig] = Field(default_factory=dict)
    vad: dict[str, ProviderConfig] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = ""
    llm: str | None = None
    asr: str | None = None
    tts: str | None = None
    vad: str | None = None


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
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    devices: dict[str, str] = Field(default_factory=dict)
    default_agent: str | None = None

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

        for name, agent in self.agents.items():
            for stage in PROVIDER_STAGES:
                ref = getattr(agent, stage)
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
                        f'agents.{name}.{stage}: unknown {stage} provider "{ref}"{hint}'
                    )

        if problems:
            raise ValueError("\n".join(problems))
        return self

    def agent_for_device(self, mac: str) -> str | None:
        """Resolve the agent name bound to a device MAC, falling back to
        default_agent for unknown devices."""
        return self.devices.get(normalize_mac(mac), self.default_agent)
