"""Replies from any OpenAI-compatible chat completions endpoint.

This is the door to local and self-hosted models: Ollama, LM Studio,
llama.cpp servers, and API gateways all speak this dialect. `base_url`
is required for that reason; pointing it at api.openai.com works too.
Endpoints that need no key (Ollama) get a placeholder, since the SDK
insists on one.
"""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from openai import AsyncOpenAI

from samtal_server.config.models import ProviderConfig
from samtal_server.providers.anthropic_llm import DEFAULT_MAX_TOKENS, resolve_api_key
from samtal_server.providers.base import (
    LlmEvent,
    LlmProvider,
    TextDelta,
    ToolCall,
    ToolChoice,
    ToolDef,
    Turn,
    Usage,
)
from samtal_server.providers.openai_endpoint import OPENAI_HOST, endpoint_host
from samtal_server.providers.registry import OptionsReader


def chat_messages(system: str, turns: Sequence[Turn]) -> list[dict[str, Any]]:
    """The pipeline's system prompt and turns in chat-completions shape.

    A turn holding tool results becomes one `tool` message per result,
    which is how this dialect answers calls; an assistant turn that
    asked for tools carries them in `tool_calls`, arguments encoded as
    a JSON string."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    for turn in turns:
        if turn.tool_results:
            messages += [
                {"role": "tool", "tool_call_id": result.tool_call_id, "content": result.content}
                for result in turn.tool_results
            ]
        elif turn.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": turn.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in turn.tool_calls
                    ],
                }
            )
        else:
            messages.append({"role": turn.role, "content": turn.content})
    return messages


def chat_tools(tools: Sequence[ToolDef]) -> list[dict[str, Any]]:
    """Tool definitions in this dialect's shape."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]


def tool_call_from_fragments(fragments: dict[str, str], index: int) -> ToolCall:
    """One accumulated streamed tool call.

    Arguments arrive as JSON text split across chunks, and a model that
    produces something that is not a JSON object is not an exception:
    the call still goes to the session, which answers it with an error
    result and gives the model another round to get it right."""
    raw = fragments["arguments"] or "{}"
    try:
        arguments = json.loads(raw)
    except ValueError:
        arguments = None
    if not isinstance(arguments, dict):
        return ToolCall(
            id=fragments["id"] or f"call_{index}",
            name=fragments["name"],
            malformed_arguments=raw,
        )
    return ToolCall(
        id=fragments["id"] or f"call_{index}", name=fragments["name"], arguments=arguments
    )


class OpenAiCompatibleLlm(LlmProvider):
    # The base_url decides: Ollama on localhost keeps everything on the
    # host, api.openai.com does not. Under server.local_only the entry
    # therefore needs its own explicit `egress` declaration.
    egress = None

    def __init__(self, base_url: str, model: str, max_tokens: int, api_key: str | None) -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or "unused")
        self._model = model
        self._max_tokens = max_tokens
        self.host = endpoint_host(base_url)
        # Whether to ask for token counts, rather than whether to read
        # them. OpenAI sends usage on a streamed call only when asked,
        # and `stream_options` is an OpenAI field that a compatible
        # server is free not to know: sending it there could fail a
        # conversation to enrich a log line, which is the wrong trade.
        # A server that reports usage unasked is still read below, so a
        # compatible endpoint that does gets its counts anyway.
        self._ask_for_usage = self.host == OPENAI_HOST

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        request: dict[str, Any] = {
            "model": self._model,
            "messages": chat_messages(system, turns),
            "max_tokens": self._max_tokens,
            "stream": True,
        }
        if tools:
            request["tools"] = chat_tools(tools)
            request["tool_choice"] = tool_choice
        if self._ask_for_usage:
            request["stream_options"] = {"include_usage": True}
        stream = await self._client.chat.completions.create(**request)

        # Tool calls stream as fragments identified by their position in
        # the call list, so they are accumulated by index and yielded
        # once the stream has ended.
        pending: dict[int, dict[str, str]] = {}
        usage: Usage | None = None
        async for chunk in stream:
            # The usage chunk is the last one and carries no choices,
            # which is why the guard below would otherwise skip it.
            if chunk.usage is not None:
                usage = Usage(
                    prompt_tokens=chunk.usage.prompt_tokens,
                    completion_tokens=chunk.usage.completion_tokens,
                )
            # Some servers interleave role-only chunks.
            if not chunk.choices or not chunk.choices[0].delta:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield TextDelta(delta.content)
            for fragment in delta.tool_calls or []:
                slot = pending.setdefault(
                    fragment.index, {"id": "", "name": "", "arguments": ""}
                )
                if fragment.id:
                    slot["id"] = fragment.id
                if fragment.function is not None:
                    if fragment.function.name:
                        slot["name"] += fragment.function.name
                    if fragment.function.arguments:
                        slot["arguments"] += fragment.function.arguments
        for index in sorted(pending):
            yield tool_call_from_fragments(pending[index], index)
        # Last, so a round's event carries what the round cost. Absent
        # from a compatible server that was not asked and does not
        # volunteer, which is a fact about that endpoint rather than a
        # failure.
        if usage is not None:
            yield usage


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
