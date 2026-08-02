"""Replies from the Anthropic API, streamed over the official SDK.

The API key is referenced by environment variable name (`api_key_env`)
per the configuration rules; without one the SDK's own resolution
applies (ANTHROPIC_API_KEY, or a logged-in profile).
"""

import os
from collections.abc import AsyncIterator, Sequence

from anthropic import AsyncAnthropic

from samtal_server.config.models import ProviderConfig
from samtal_server.providers.base import LlmProvider, ProviderError, Turn
from samtal_server.providers.registry import OptionsReader

# Spoken replies are short; this caps runaways, not conversation.
DEFAULT_MAX_TOKENS = 1024


class AnthropicLlm(LlmProvider):
    def __init__(self, model: str, max_tokens: int, api_key: str | None) -> None:
        self._client = AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()
        self._model = model
        self._max_tokens = max_tokens

    async def stream(self, system: str, turns: Sequence[Turn]) -> AsyncIterator[str]:
        request: dict[str, object] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [{"role": turn.role, "content": turn.content} for turn in turns],
        }
        if system:
            request["system"] = system
        async with self._client.messages.stream(**request) as stream:
            async for text in stream.text_stream:
                yield text


def resolve_api_key(label: str, api_key_env: str | None) -> str | None:
    """The secret behind an `api_key_env` reference, or None to leave
    resolution to the SDK. A named but unset variable fails the build,
    because at request time it would fail every conversation."""
    if api_key_env is None:
        return None
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise ProviderError(
            f"{label}: api_key_env names {api_key_env}, but it is not set in the environment"
        )
    return api_key


def build(label: str, config: ProviderConfig) -> AnthropicLlm:
    options = OptionsReader(label, config)
    provider = AnthropicLlm(
        model=options.required_string("model"),
        max_tokens=options.integer("max_tokens", DEFAULT_MAX_TOKENS),
        api_key=resolve_api_key(label, config.api_key_env),
    )
    options.finish()
    return provider
