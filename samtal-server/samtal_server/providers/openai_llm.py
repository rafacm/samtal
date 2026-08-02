"""Replies from any OpenAI-compatible chat completions endpoint.

This is the door to local and self-hosted models: Ollama, LM Studio,
llama.cpp servers, and API gateways all speak this dialect. `base_url`
is required for that reason; pointing it at api.openai.com works too.
Endpoints that need no key (Ollama) get a placeholder, since the SDK
insists on one.
"""

from collections.abc import AsyncIterator, Sequence

from openai import AsyncOpenAI

from samtal_server.config.models import ProviderConfig
from samtal_server.providers.anthropic_llm import DEFAULT_MAX_TOKENS, resolve_api_key
from samtal_server.providers.base import LlmProvider, Turn
from samtal_server.providers.registry import OptionsReader


def chat_messages(system: str, turns: Sequence[Turn]) -> list[dict[str, str]]:
    """The pipeline's system prompt and turns in chat-completions shape."""
    messages = [{"role": "system", "content": system}] if system else []
    return messages + [{"role": turn.role, "content": turn.content} for turn in turns]


class OpenAiCompatibleLlm(LlmProvider):
    def __init__(self, base_url: str, model: str, max_tokens: int, api_key: str | None) -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or "unused")
        self._model = model
        self._max_tokens = max_tokens

    async def stream(self, system: str, turns: Sequence[Turn]) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=chat_messages(system, turns),  # type: ignore[arg-type]
            max_tokens=self._max_tokens,
            stream=True,
        )
        async for chunk in stream:
            # Some servers interleave role-only or usage chunks.
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


def build(label: str, config: ProviderConfig) -> OpenAiCompatibleLlm:
    options = OptionsReader(label, config)
    provider = OpenAiCompatibleLlm(
        base_url=options.required_string("base_url"),
        model=options.required_string("model"),
        max_tokens=options.integer("max_tokens", DEFAULT_MAX_TOKENS),
        api_key=resolve_api_key(label, config.api_key_env),
    )
    options.finish()
    return provider
