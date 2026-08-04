"""Building providers from their configuration entries.

Each stage maps type names to factories. Heavyweight implementations
import their engine inside the factory, so the core install only pays
for what the configuration references, and a missing optional
dependency becomes an error naming the extra to install rather than an
ImportError from the middle of a request.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from samtal_server.config import Config
from samtal_server.config.models import PROVIDER_STAGES, ProviderConfig
from samtal_server.providers.base import (
    AsrProvider,
    LlmProvider,
    ProviderError,
    TtsProvider,
    VadProvider,
)


class OptionsReader:
    """Typed access to a provider's options, with errors that name the
    configuration entry. Providers reject options they do not know, so
    a typo fails at startup instead of silently configuring nothing."""

    def __init__(self, label: str, config: ProviderConfig) -> None:
        self._label = label
        self._pending = config.options

    def string(self, key: str, default: str | None = None) -> str | None:
        value = self._pending.pop(key, default)
        if value is None or isinstance(value, str):
            return value
        raise ProviderError(f'{self._label}: option "{key}" must be a string')

    def required_string(self, key: str) -> str:
        value = self.string(key)
        if value is None or not value.strip():
            raise ProviderError(f'{self._label}: option "{key}" is required')
        return value

    def number(self, key: str, default: float) -> float:
        value = self._pending.pop(key, default)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ProviderError(f'{self._label}: option "{key}" must be a number')
        return float(value)

    def integer(self, key: str, default: int) -> int:
        value = self._pending.pop(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProviderError(f'{self._label}: option "{key}" must be an integer')
        return value

    def boolean(self, key: str, default: bool) -> bool:
        value = self._pending.pop(key, default)
        if not isinstance(value, bool):
            raise ProviderError(f'{self._label}: option "{key}" must be true or false')
        return value

    def numbers(self, key: str) -> list[float] | None:
        """A non-empty list of numbers, with a single number taken as a
        list of one; None when absent."""
        value = self._pending.pop(key, None)
        if value is None:
            return None
        if not isinstance(value, bool) and isinstance(value, int | float):
            return [float(value)]
        if (
            isinstance(value, list)
            and value
            and all(
                not isinstance(item, bool) and isinstance(item, int | float)
                for item in value
            )
        ):
            return [float(item) for item in value]
        raise ProviderError(
            f'{self._label}: option "{key}" must be a number or a non-empty list of numbers'
        )

    def mapping(self, key: str) -> dict[str, object]:
        """A nested option written as a YAML mapping, empty when absent."""
        value = self._pending.pop(key, None)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ProviderError(f'{self._label}: option "{key}" must be a mapping')
        return {str(name): item for name, item in value.items()}

    def finish(self) -> None:
        if self._pending:
            unknown = ", ".join(sorted(self._pending))
            raise ProviderError(f"{self._label}: unknown option(s): {unknown}")


Factory = Callable[[str, ProviderConfig], object]


def _silero(label: str, config: ProviderConfig) -> object:
    from samtal_server.providers import silero

    return silero.build(label, config)


def _faster_whisper(label: str, config: ProviderConfig) -> object:
    try:
        from samtal_server.providers import faster_whisper
    except ImportError as exc:
        raise ProviderError(
            f'{label}: type "faster_whisper" needs the faster-whisper extra; '
            f"install it with: uv sync --extra faster-whisper"
        ) from exc
    return faster_whisper.build(label, config)


def _anthropic(label: str, config: ProviderConfig) -> object:
    from samtal_server.providers import anthropic_llm

    return anthropic_llm.build(label, config)


def _openai_compatible(label: str, config: ProviderConfig) -> object:
    from samtal_server.providers import openai_llm

    return openai_llm.build(label, config)


def _piper(label: str, config: ProviderConfig) -> object:
    try:
        from samtal_server.providers import piper_tts
    except ImportError as exc:
        raise ProviderError(
            f'{label}: type "piper" needs the piper extra; '
            f"install it with: uv sync --extra piper"
        ) from exc
    return piper_tts.build(label, config)


def _factories() -> dict[str, dict[str, Factory]]:
    # Imported here rather than at module top because the implementation
    # modules import the OptionsReader above; the table itself is tiny.
    from samtal_server.providers import mock

    return {
        "llm": {
            "mock": mock.build_llm,
            "anthropic": _anthropic,
            "openai_compatible": _openai_compatible,
        },
        "asr": {"mock": mock.build_asr, "faster_whisper": _faster_whisper},
        "tts": {"mock": mock.build_tts, "piper": _piper},
        "vad": {"mock": mock.build_vad, "silero": _silero},
    }


def build_provider(stage: str, name: str, config: ProviderConfig) -> object:
    """Build the provider behind `providers.<stage>.<name>`, raising
    ProviderError for unknown types, bad options, or missing extras."""
    label = f"providers.{stage}.{name}"
    factory = _factories()[stage].get(config.type)
    if factory is None:
        known = ", ".join(sorted(_factories()[stage]))
        raise ProviderError(
            f'{label}: unknown {stage} provider type "{config.type}" (known types: {known})'
        )
    return factory(label, config)


@dataclass(frozen=True)
class AgentProviders:
    """Everything a session needs to hold a conversation as one agent."""

    prompt: str
    llm: LlmProvider
    asr: AsrProvider
    tts: TtsProvider
    vad: VadProvider


def build_agent_providers(config: Config) -> dict[str, AgentProviders]:
    """Build every provider the configured agents reference, sharing one
    instance per named entry across agents. Agents are read through their
    effective view, so a stage comes from the agent or from agent_defaults.
    Runs at startup, so a bad provider configuration, a missing extra, or
    an agent without a full pipeline fails the boot rather than the first
    conversation."""
    built: dict[tuple[str, str], object] = {}

    def get(stage: str, agent_name: str) -> object:
        provider_name, _ = config.provider_for_agent(agent_name, stage)
        if provider_name is None:
            raise ProviderError(
                f"agents.{agent_name}: no {stage} provider is named, and "
                f"agent_defaults.{stage} names none either; the conversation "
                f"pipeline needs all of: {', '.join(PROVIDER_STAGES)}"
            )
        key = (stage, provider_name)
        if key not in built:
            provider_config = getattr(config.providers, stage)[provider_name]
            built[key] = build_provider(stage, provider_name, provider_config)
        return built[key]

    return {
        name: AgentProviders(
            prompt=agent.prompt,
            llm=cast(LlmProvider, get("llm", name)),
            asr=cast(AsrProvider, get("asr", name)),
            tts=cast(TtsProvider, get("tts", name)),
            vad=cast(VadProvider, get("vad", name)),
        )
        for name, agent in config.agents.items()
    }
