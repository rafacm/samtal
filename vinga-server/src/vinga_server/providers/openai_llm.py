"""Replies from any OpenAI-compatible chat completions endpoint.

This is the door to local and self-hosted models: Ollama, LM Studio,
llama.cpp servers, and API gateways all speak this dialect. `base_url`
is required for that reason; pointing it at api.openai.com works too.
Endpoints that need no key (Ollama) get a placeholder, since the SDK
insists on one.

What this type accepts is declared once, as `OpenaiCompatibleOptions` in
`config/provider_options.py`, and reaches the builder below already
validated. It is the one declaration whose door stays open, and this is
the module that makes the opening mean something: an option the model
does not declare is not dropped and not refused, it rides into the
outgoing request body through the SDK's own `extra_body`, under the
fields this module composes for every call (#88).
"""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from openai import AsyncOpenAI

from vinga_server.config.models import ProviderConfig
from vinga_server.config.provider_options import (
    RESERVED_REQUEST_FIELDS,
    OpenaiCompatibleOptions,
)
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
    DEFAULT_TIMEOUT_S,
    MAX_RETRIES,
    OPENAI_FAILURES,
    call_failure,
    resolve_api_key,
)
from vinga_server.providers.openai_endpoint import (
    OPENAI_HOST,
    endpoint_host,
    parse_base_url,
)

# How this provider names itself in the message a failed request
# carries. The entry's own name and host reach the event beside it, so
# this says which dialect was speaking rather than which entry.
LABEL = "openai compatible"


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

    def __init__(
        self,
        base_url: str,
        model: str,
        max_tokens: int,
        api_key: str | None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: AsyncOpenAI | None = None,
        passthrough: Mapping[str, object] | None = None,
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
        self._client = (
            client
            if client is not None
            else AsyncOpenAI(
                base_url=base_url,
                api_key=api_key or "unused",
                timeout=timeout_s,
                max_retries=MAX_RETRIES,
            )
        )
        self.model = model
        self._max_tokens = max_tokens
        # The options this repository does not declare, on their way to
        # a server that does. Filtered rather than trusted: the model
        # refuses these names when the entry is written, and this is the
        # seam a provider constructed directly comes through, so the
        # promise that a passthrough key cannot rewrite the request is
        # kept on both sides of it. The SDK merges `extra_body` OVER the
        # fields below rather than under them, which is what makes the
        # filtering the load-bearing half rather than the merge order.
        self._passthrough = {
            key: value
            for key, value in (passthrough or {}).items()
            if key not in RESERVED_REQUEST_FIELDS
        }
        self.host = endpoint_host(base_url)
        # Whether to ask for token counts, rather than whether to read
        # them. OpenAI sends usage on a streamed call only when asked,
        # and `stream_options` is an OpenAI field that a compatible
        # server is free not to know: sending it there could fail a
        # conversation to enrich a log line, which is the wrong trade.
        # A server that reports usage unasked is still read below, so a
        # compatible endpoint that does gets its counts anyway.
        self._ask_for_usage = self.host == OPENAI_HOST

    async def close(self) -> None:
        """Shut the SDK client's connection pool. An entry an apply has
        rewritten is built again as a new object, and this one holds
        sockets to a host nothing is going to ask anything of again."""
        await self._client.close()

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages(system, turns),
            "max_tokens": self._max_tokens,
            "stream": True,
        }
        if tools:
            request["tools"] = chat_tools(tools)
            request["tool_choice"] = tool_choice
        if self._ask_for_usage:
            request["stream_options"] = {"include_usage": True}
        # And the entry's own options, into the same body the fields
        # above go into. `extra_body` is this SDK's escape door: what it
        # holds is merged into the outgoing JSON at the top level, which
        # is where a server-specific parameter belongs and is the only
        # way to send one at all, since `create` takes the dialect's
        # fields by name and nothing else.
        #
        # An option that reached configuration and then went nowhere is
        # the failure this type's model exists to remove: the reader it
        # replaced refused every leftover, so nothing was ever silently
        # ignored, and a hatch that accepted a key without sending it
        # would be a regression dressed as a feature (#88).
        if self._passthrough:
            request["extra_body"] = dict(self._passthrough)

        # Tool calls stream as fragments identified by their position in
        # the call list, so they are accumulated by index and yielded
        # once the stream has ended. That buffering is why the first
        # chunk off the wire is announced below: a round that streams
        # only fragments would otherwise look, from outside, exactly
        # like a request the server never answered (#68).
        pending: dict[int, dict[str, str]] = {}
        usage: Usage | None = None
        started = False
        # The request and the chunks after it are both inside: an
        # endpoint can stop answering at either, and both are a failed
        # provider call rather than a bug here. Cancellation and genuine
        # bugs, and another vendor's SDK error, are outside
        # OPENAI_FAILURES and pass through as themselves.
        failure: ProviderCallError | None = None
        try:
            stream = await self._client.chat.completions.create(**request)
            async for chunk in stream:
                if not started:
                    started = True
                    yield StreamStarted()
                # The usage chunk is the last one and carries no
                # choices, which is why the guard below would otherwise
                # skip it.
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
        except OPENAI_FAILURES as exc:
            failure = call_failure(LABEL, exc)
        # Raised out here rather than in the except arm, so the SDK
        # exception is not even the new error's `__context__`: `from
        # None` suppresses its rendering but leaves it reachable, and
        # what it can carry is the reason the message is metadata only.
        if failure is not None:
            raise failure from None
        for index in sorted(pending):
            yield tool_call_from_fragments(pending[index], index)
        # Last, so a round's event carries what the round cost. Absent
        # from a compatible server that was not asked and does not
        # volunteer, which is a fact about that endpoint rather than a
        # failure.
        if usage is not None:
            yield usage


def build(
    label: str, config: ProviderConfig, options: OpenaiCompatibleOptions
) -> OpenAiCompatibleLlm:
    """The entry's validated options as the provider's own arguments.

    One rule and one lookup, which is what this seam has left. The rule
    is the URL's shape, and it stays here rather than moving onto the
    field: `required_string` is all the model replaces, and what
    `parse_base_url` asks is a question about an endpoint rather than
    about a string, asked by all three stages that speak this dialect
    from the one module that owns it. The lookup is the credential,
    which is the one thing an options model cannot hold.

    Nothing here refuses an option. Every one of them was checked
    against `OpenaiCompatibleOptions` before this was called, which is
    the ordering the reader's `finish()` used to hold, and what that
    model did not recognize travels rather than being refused.
    """
    # The answer is deliberately discarded: the constructor derives the
    # host it needs from the same URL for the failure event, and whether
    # that host is OpenAI's is what decides usage, so threading this
    # boolean in would encode one predicate twice. What the call is here
    # for is the refusal, which belongs at build like every other thing
    # this factory refuses rather than at the first request of the first
    # conversation.
    parse_base_url(label, options.base_url)
    return OpenAiCompatibleLlm(
        base_url=options.base_url,
        model=options.model,
        max_tokens=options.max_tokens,
        api_key=resolve_api_key(label, config.api_key_env),
        passthrough=options.model_extra,
    )
