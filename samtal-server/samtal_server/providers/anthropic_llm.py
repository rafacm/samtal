"""Replies from the Anthropic API, streamed over the official SDK.

The API key is referenced by environment variable name (`api_key_env`)
per the configuration rules; without one the SDK's own resolution
applies (ANTHROPIC_API_KEY, or a logged-in profile).
"""

import os
from collections.abc import AsyncIterator, Sequence
from typing import Any

from anthropic import AsyncAnthropic

from samtal_server.config.models import ProviderConfig
from samtal_server.config.secrets import stored_provider_secret
from samtal_server.providers.base import (
    LlmEvent,
    LlmProvider,
    ProviderError,
    StreamStarted,
    TextDelta,
    ToolCall,
    ToolChoice,
    ToolDef,
    Turn,
    Usage,
)
from samtal_server.providers.registry import OptionsReader

# Spoken replies are short; this caps runaways, not conversation.
DEFAULT_MAX_TOKENS = 1024

# The one host this type reaches; it has no base_url to point anywhere
# else.
API_HOST = "api.anthropic.com"

# The credential slot every provider type here fills, and the name a
# stored secret is written under. The seam is `<slot>_env` in the
# configuration and `<slot>` in the store, which is what lets one
# resolver serve both.
API_KEY_SLOT = "api_key"


def anthropic_messages(turns: Sequence[Turn]) -> list[dict[str, Any]]:
    """The pipeline's turns as Anthropic messages.

    Text turns stay plain strings. An assistant turn that asked for
    tools becomes content blocks, its spoken preamble first and one
    `tool_use` block per call; the turn answering them becomes a user
    message of `tool_result` blocks, which is where this API puts them.
    """
    messages: list[dict[str, Any]] = []
    for turn in turns:
        if turn.tool_results:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": result.tool_call_id,
                            "content": result.content,
                            "is_error": result.is_error,
                        }
                        for result in turn.tool_results
                    ],
                }
            )
        elif turn.tool_calls:
            blocks: list[dict[str, Any]] = []
            if turn.content:
                blocks.append({"type": "text", "text": turn.content})
            blocks += [
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                for call in turn.tool_calls
            ]
            messages.append({"role": "assistant", "content": blocks})
        else:
            messages.append({"role": turn.role, "content": turn.content})
    return messages


def anthropic_tools(tools: Sequence[ToolDef]) -> list[dict[str, Any]]:
    """Tool definitions in this API's shape. MCP's JSON Schema is what
    `input_schema` already holds, so nothing is translated."""
    return [
        {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
        for tool in tools
    ]


class AnthropicLlm(LlmProvider):
    # Every request carries the conversation to the vendor's API.
    egress = True
    host = API_HOST

    def __init__(self, model: str, max_tokens: int, api_key: str | None) -> None:
        self._client = AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()
        self._model = model
        self._max_tokens = max_tokens

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        request: dict[str, object] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": anthropic_messages(turns),
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = anthropic_tools(tools)
            request["tool_choice"] = {"type": tool_choice}
        async with self._client.messages.stream(**request) as stream:
            # The stream's own events rather than its text_stream view,
            # because text_stream hides everything that is not a text
            # delta: a round streaming only tool-call fragments would
            # yield nothing at all until the end, and the session's
            # first-token watchdog could not tell it from a request the
            # API never answered (#68). The first event off the wire is
            # announced whatever it holds.
            started = False
            async for event in stream:
                if not started:
                    started = True
                    yield StreamStarted()
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield TextDelta(event.delta.text)
            # Tool calls come from the assembled message rather than the
            # deltas: their arguments arrive as JSON fragments, and the
            # SDK has already stitched them together by this point.
            message = await stream.get_final_message()
        for block in message.content:
            if block.type == "tool_use":
                yield ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {}))
        # Last, so a round's event carries what the round cost. This
        # API reports usage on every streamed message without being
        # asked, which the OpenAI dialect does not.
        yield Usage(
            prompt_tokens=message.usage.input_tokens,
            completion_tokens=message.usage.output_tokens,
        )


def resolve_api_key(label: str, api_key_env: str | None) -> str | None:
    """The credential for the `api_key` slot of the provider being
    built, or None to leave resolution to the SDK.

    Two sources, in one place, because a provider must not care which
    one a deployment used: a secret stored in the configuration database
    for this entry's `api_key` slot, or the environment variable an
    `api_key_env` reference names. A named but unset variable fails the
    build, because at request time it would fail every conversation.

    Ciphertext wins, and the reference it shadows is not read at all:
    set-secret is the later and more deliberate act, and an unset
    variable left behind it must not fail the boot the stored secret was
    set to fix. The value goes straight into the client here and lands
    on no model on the way."""
    stored = stored_provider_secret(API_KEY_SLOT)
    if stored is not None:
        return stored
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
