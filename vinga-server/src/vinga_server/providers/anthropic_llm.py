"""Replies from the Anthropic API, streamed over the official SDK.

The API key is referenced by environment variable name (`api_key_env`)
per the configuration rules; without one the SDK's own resolution
applies (ANTHROPIC_API_KEY, or a logged-in profile).
"""

from collections.abc import AsyncIterator, Sequence
from typing import Any

from anthropic import AsyncAnthropic

from vinga_server.config.models import ProviderConfig
from vinga_server.providers.base import (
    LlmEvent,
    LlmProvider,
    ProviderCallError,
    StreamStarted,
    TextDelta,
    ToolCall,
    ToolChoice,
    ToolDef,
    Turn,
    Usage,
)
from vinga_server.providers.kit import (
    ANTHROPIC_FAILURES,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TIMEOUT_S,
    MAX_RETRIES,
    call_failure,
    resolve_api_key,
)
from vinga_server.providers.registry import OptionsReader

# How this provider names itself in the message a failed request
# carries.
LABEL = "anthropic"

# The one host this type reaches; it has no base_url to point anywhere
# else.
API_HOST = "api.anthropic.com"


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

    def __init__(
        self,
        model: str,
        max_tokens: int,
        api_key: str | None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: AsyncAnthropic | None = None,
    ) -> None:
        # One client per provider entry, so its connection pool is
        # reused across turns and sessions, and the seam the other cloud
        # providers already have: a test hands its own client in through
        # the front door rather than assigning over this attribute.
        #
        # The timeout is a per-operation transport bound with the SDK's
        # retries off, not a wall-clock deadline for the reply: a stream
        # that keeps delivering may legitimately run longer, and the
        # session's first-token watchdog is what bounds a stream that
        # produces nothing. Not a configuration option, deliberately;
        # until now these clients had no bound at all, and a deployment
        # needing a nonstandard one is a change with its own issue.
        #
        # A None api_key is the SDK resolving its own (ANTHROPIC_API_KEY
        # or a logged-in profile), which is what it does with the
        # argument absent.
        self._client = (
            client
            if client is not None
            else AsyncAnthropic(
                api_key=api_key, timeout=timeout_s, max_retries=MAX_RETRIES
            )
        )
        self.model = model
        self._max_tokens = max_tokens

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        request: dict[str, object] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "messages": anthropic_messages(turns),
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = anthropic_tools(tools)
            request["tool_choice"] = {"type": tool_choice}
        # Everything that waits on the wire is inside: the request, the
        # events after it, and the assembled message at the end are
        # three separate places this API can stop answering, and all
        # three are a failed provider call rather than a bug here.
        # Cancellation, a genuine bug, and another vendor's SDK error are
        # outside ANTHROPIC_FAILURES and pass through as themselves.
        failure: ProviderCallError | None = None
        try:
            async with self._client.messages.stream(**request) as stream:
                # The stream's own events rather than its text_stream
                # view, because text_stream hides everything that is not
                # a text delta: a round streaming only tool-call
                # fragments would yield nothing at all until the end,
                # and the session's first-token watchdog could not tell
                # it from a request the API never answered (#68). The
                # first event off the wire is announced whatever it
                # holds.
                started = False
                async for event in stream:
                    if not started:
                        started = True
                        yield StreamStarted()
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield TextDelta(event.delta.text)
                # Tool calls come from the assembled message rather than
                # the deltas: their arguments arrive as JSON fragments,
                # and the SDK has already stitched them together by this
                # point.
                message = await stream.get_final_message()
        except ANTHROPIC_FAILURES as exc:
            failure = call_failure(LABEL, exc)
        # Raised out here rather than in the except arm, so the SDK
        # exception is not even the new error's `__context__`: `from
        # None` suppresses its rendering but leaves it reachable, and
        # what it can carry is the reason the message is metadata only.
        if failure is not None:
            raise failure from None
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


def build(label: str, config: ProviderConfig) -> AnthropicLlm:
    options = OptionsReader(label, config)
    provider = AnthropicLlm(
        model=options.required_string("model"),
        max_tokens=options.integer("max_tokens", DEFAULT_MAX_TOKENS),
        api_key=resolve_api_key(label, config.api_key_env),
    )
    options.finish()
    return provider
