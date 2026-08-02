"""Building providers from their configuration entries.

Each stage maps type names to factories. Heavyweight implementations
import their engine inside the factory, so the core install only pays
for what the configuration references, and a missing optional
dependency becomes an error naming the extra to install rather than an
ImportError from the middle of a request.
"""

from collections.abc import Callable

from samtal_server.config.models import ProviderConfig
from samtal_server.providers.base import ProviderError


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

    def finish(self) -> None:
        if self._pending:
            unknown = ", ".join(sorted(self._pending))
            raise ProviderError(f"{self._label}: unknown option(s): {unknown}")


Factory = Callable[[str, ProviderConfig], object]


def _factories() -> dict[str, dict[str, Factory]]:
    # Imported here rather than at module top because mock imports the
    # OptionsReader above; the table itself is tiny.
    from samtal_server.providers import mock

    return {
        "llm": {"mock": mock.build_llm},
        "asr": {"mock": mock.build_asr},
        "tts": {"mock": mock.build_tts},
        "vad": {"mock": mock.build_vad},
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
