"""Tool calling on the wire, per provider, and what a stream does when
the wire fails.

The mapping between the neutral tool model and each API's shape is
pure-function territory and tested as such. Streaming is exercised
against a stub client rather than a live endpoint, because what matters
here is the request that goes out and the events that come back, not
the transport. The same stubs answer the other question a streaming
adapter has to answer: a request that never lands, and a connection
that drops after the first chunk, both leave as the taxonomy the
pipeline classifies by (#137).
"""

import asyncio
from typing import Any

import anthropic
import httpx
import openai
import pytest

from samtal_server.providers import (
    ProviderCallError,
    ProviderCallTimeout,
    StreamStarted,
    TextDelta,
    ToolCall,
    ToolDef,
    ToolResult,
    Turn,
    Usage,
)
from samtal_server.providers.anthropic_llm import (
    AnthropicLlm,
    anthropic_messages,
    anthropic_tools,
)
from samtal_server.providers.openai_llm import (
    OpenAiCompatibleLlm,
    chat_messages,
    chat_tools,
    tool_call_from_fragments,
)
from tests.support.llm_sdk import (
    FakeBlock,
    FakeChoice,
    FakeChunk,
    FakeCompletions,
    FakeDelta,
    FakeFragment,
    FakeFunction,
    FakeMessage,
    FakeMessages,
    FakeStream,
)

WEATHER = ToolDef(
    name="weather__forecast",
    description="Tomorrow's weather",
    input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
)

TOOL_EXCHANGE = [
    Turn("user", "will it rain"),
    Turn(
        "assistant",
        "Let me check.",
        tool_calls=(ToolCall(id="t1", name="weather__forecast", arguments={"city": "Malmo"}),),
    ),
    Turn("tool", "", tool_results=(ToolResult(tool_call_id="t1", content="rain"),)),
]


def test_anthropic_renders_a_tool_exchange_as_content_blocks() -> None:
    assert anthropic_messages(TOOL_EXCHANGE) == [
        {"role": "user", "content": "will it rain"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me check."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "weather__forecast",
                    "input": {"city": "Malmo"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "rain",
                    "is_error": False,
                }
            ],
        },
    ]


def test_anthropic_carries_the_error_flag_of_a_failed_tool() -> None:
    turns = [Turn("tool", "", tool_results=(ToolResult("t1", "the tool timed out", True),))]
    (message,) = anthropic_messages(turns)
    assert message["content"][0]["is_error"] is True


def test_anthropic_omits_an_empty_preamble() -> None:
    # A model that calls a tool without saying anything first must not
    # produce an empty text block, which the API rejects.
    turns = [Turn("assistant", "", tool_calls=(ToolCall(id="t1", name="x"),))]
    (message,) = anthropic_messages(turns)
    assert [block["type"] for block in message["content"]] == ["tool_use"]


def test_anthropic_tool_definitions_pass_the_schema_through() -> None:
    assert anthropic_tools([WEATHER]) == [
        {
            "name": "weather__forecast",
            "description": "Tomorrow's weather",
            "input_schema": WEATHER.input_schema,
        }
    ]


def anthropic_with(texts: list[str], blocks: list[FakeBlock]) -> tuple[AnthropicLlm, FakeMessages]:
    messages = FakeMessages(FakeStream(texts, FakeMessage(blocks)))
    llm = AnthropicLlm(
        model="claude-sonnet-5",
        max_tokens=64,
        api_key="sk-test",
        client=type("Client", (), {"messages": messages})(),  # type: ignore[arg-type]
    )
    return llm, messages


async def test_anthropic_yields_speech_then_the_tool_calls_it_asked_for() -> None:
    llm, messages = anthropic_with(
        ["Let me ", "check."],
        [
            FakeBlock(type="text"),
            FakeBlock(type="tool_use", id="t1", name="weather__forecast", input={"city": "Lund"}),
        ],
    )
    events = [
        event async for event in llm.stream("be brief", [Turn("user", "rain?")], [WEATHER])
    ]
    assert events == [
        # The first chunk off the wire, announced whatever it holds.
        StreamStarted(),
        TextDelta("Let me "),
        TextDelta("check."),
        ToolCall(id="t1", name="weather__forecast", arguments={"city": "Lund"}),
        # This API reports what a generation cost without being asked.
        Usage(prompt_tokens=11, completion_tokens=7),
    ]
    assert messages.request["tools"] == anthropic_tools([WEATHER])
    assert messages.request["tool_choice"] == {"type": "auto"}
    assert messages.request["system"] == "be brief"


async def test_anthropic_passes_a_forbidden_tool_choice_through() -> None:
    llm, messages = anthropic_with(["Fine."], [FakeBlock(type="text")])
    events = [
        event
        async for event in llm.stream("", [Turn("user", "hi")], [WEATHER], tool_choice="none")
    ]
    assert events == [
        StreamStarted(),
        TextDelta("Fine."),
        Usage(prompt_tokens=11, completion_tokens=7),
    ]
    # The definitions still go out, so the conversation the model sees
    # does not change shape between rounds; only calling is forbidden.
    assert messages.request["tools"]
    assert messages.request["tool_choice"] == {"type": "none"}


async def test_anthropic_announces_the_wire_before_a_tool_only_round() -> None:
    """A round that streams only a tool call yields no text and its
    call only after the stream ends, so the announcement is the one
    event that tells the session's watchdog this stream is alive."""
    llm, _ = anthropic_with(
        [],
        [FakeBlock(type="tool_use", id="t1", name="weather__forecast", input={"city": "Lund"})],
    )
    events = [event async for event in llm.stream("", [Turn("user", "rain?")], [WEATHER])]
    assert events == [
        StreamStarted(),
        ToolCall(id="t1", name="weather__forecast", arguments={"city": "Lund"}),
        Usage(prompt_tokens=11, completion_tokens=7),
    ]


async def test_anthropic_sends_no_tool_fields_when_there_are_no_tools() -> None:
    llm, messages = anthropic_with(["Hello."], [FakeBlock(type="text")])
    [event async for event in llm.stream("", [Turn("user", "hi")])]
    assert "tools" not in messages.request
    assert "tool_choice" not in messages.request


def test_openai_renders_a_tool_exchange_as_chat_messages() -> None:
    assert chat_messages("be brief", TOOL_EXCHANGE) == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "will it rain"},
        {
            "role": "assistant",
            "content": "Let me check.",
            "tool_calls": [
                {
                    "id": "t1",
                    "type": "function",
                    "function": {
                        "name": "weather__forecast",
                        "arguments": '{"city": "Malmo"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "content": "rain"},
    ]


def test_openai_tool_definitions_wrap_the_schema_as_parameters() -> None:
    assert chat_tools([WEATHER]) == [
        {
            "type": "function",
            "function": {
                "name": "weather__forecast",
                "description": "Tomorrow's weather",
                "parameters": WEATHER.input_schema,
            },
        }
    ]


def test_streamed_argument_fragments_accumulate_into_one_call() -> None:
    fragments = {"id": "call_a", "name": "weather__forecast", "arguments": '{"ci'}
    fragments["arguments"] += 'ty": "Lund"}'
    assert tool_call_from_fragments(fragments, 0) == ToolCall(
        id="call_a", name="weather__forecast", arguments={"city": "Lund"}
    )


def test_a_call_with_no_arguments_at_all_is_still_a_call() -> None:
    assert tool_call_from_fragments({"id": "c", "name": "now", "arguments": ""}, 0).arguments == {}


def test_malformed_arguments_survive_as_a_call_the_session_can_refuse() -> None:
    # Small local models do produce broken JSON. It must reach the
    # session as a call, so the model gets an error result and another
    # round, rather than an exception that ends the reply.
    call = tool_call_from_fragments({"id": "c", "name": "now", "arguments": "{city: Lund"}, 0)
    assert call.arguments == {}
    assert call.malformed_arguments == "{city: Lund"


def test_arguments_that_are_not_an_object_are_malformed_too() -> None:
    call = tool_call_from_fragments({"id": "c", "name": "now", "arguments": '"Lund"'}, 0)
    assert call.malformed_arguments == '"Lund"'


def openai_with(chunks: list[FakeChunk]) -> tuple[OpenAiCompatibleLlm, FakeCompletions]:
    completions = FakeCompletions(chunks)
    llm = OpenAiCompatibleLlm(
        base_url="http://localhost:11434/v1",
        model="qwen3:8b",
        max_tokens=64,
        api_key=None,
        client=type(  # type: ignore[arg-type]
            "Client", (), {"chat": type("Chat", (), {"completions": completions})()}
        )(),
    )
    return llm, completions


async def test_openai_yields_speech_then_the_accumulated_tool_calls() -> None:
    llm, completions = openai_with(
        [
            FakeChunk([FakeChoice(FakeDelta(content="Let me "))]),
            FakeChunk([FakeChoice(FakeDelta(content="check."))]),
            # A usage-only chunk, which some servers interleave.
            FakeChunk([]),
            FakeChunk(
                [
                    FakeChoice(
                        FakeDelta(
                            tool_calls=[
                                FakeFragment(
                                    index=0,
                                    id="call_a",
                                    function=FakeFunction(name="weather__forecast"),
                                )
                            ]
                        )
                    )
                ]
            ),
            FakeChunk(
                [
                    FakeChoice(
                        FakeDelta(
                            tool_calls=[
                                FakeFragment(index=0, function=FakeFunction(arguments='{"ci'))
                            ]
                        )
                    )
                ]
            ),
            FakeChunk(
                [
                    FakeChoice(
                        FakeDelta(
                            tool_calls=[
                                FakeFragment(
                                    index=0, function=FakeFunction(arguments='ty": "Lund"}')
                                )
                            ]
                        )
                    )
                ]
            ),
        ]
    )
    events = [
        event async for event in llm.stream("be brief", [Turn("user", "rain?")], [WEATHER])
    ]
    assert events == [
        # The first chunk off the wire, announced whatever it holds.
        StreamStarted(),
        TextDelta("Let me "),
        TextDelta("check."),
        ToolCall(id="call_a", name="weather__forecast", arguments={"city": "Lund"}),
    ]
    assert completions.request["tools"] == chat_tools([WEATHER])
    assert completions.request["tool_choice"] == "auto"


async def test_openai_announces_the_wire_before_a_tool_only_round() -> None:
    """The same guarantee for the other dialect: fragments are buffered
    until the stream ends, and the announcement is what proves the
    stream was delivering all along."""
    llm, _ = openai_with(
        [
            FakeChunk(
                [
                    FakeChoice(
                        FakeDelta(
                            tool_calls=[
                                FakeFragment(
                                    index=0,
                                    id="call_a",
                                    function=FakeFunction(
                                        name="weather__forecast",
                                        arguments='{"city": "Lund"}',
                                    ),
                                )
                            ]
                        )
                    )
                ]
            ),
        ]
    )
    events = [event async for event in llm.stream("", [Turn("user", "rain?")], [WEATHER])]
    assert events == [
        StreamStarted(),
        ToolCall(id="call_a", name="weather__forecast", arguments={"city": "Lund"}),
    ]


async def test_openai_passes_a_forbidden_tool_choice_through() -> None:
    llm, completions = openai_with([FakeChunk([FakeChoice(FakeDelta(content="Fine."))])])
    events = [
        event
        async for event in llm.stream("", [Turn("user", "hi")], [WEATHER], tool_choice="none")
    ]
    # No usage chunk from this endpoint, and no Usage event: what the
    # server did not report is not invented.
    assert events == [StreamStarted(), TextDelta("Fine.")]
    assert completions.request["tools"]
    assert completions.request["tool_choice"] == "none"


async def test_openai_sends_no_tool_fields_when_there_are_no_tools() -> None:
    llm, completions = openai_with([FakeChunk([FakeChoice(FakeDelta(content="Hello."))])])
    [event async for event in llm.stream("", [Turn("user", "hi")])]
    assert "tools" not in completions.request
    assert "tool_choice" not in completions.request


# --- when the wire fails (#137) --------------------------------------

REQUEST = httpx.Request("POST", "https://api.example.invalid/v1/messages")

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It stands in for what a compatible endpoint can
# echo back into a message or a response body.
SENTINEL = "sk-test-4f8b2c9e-never-a-real-credential"


def status_error(sdk: Any, status: int, message: str) -> Exception:
    """One SDK's HTTP failure, carrying whatever the far end said."""
    return sdk.APIStatusError(
        message,
        response=httpx.Response(status, request=REQUEST, json={"error": {"message": message}}),
        body=None,
    )


def chain(exc: BaseException) -> str:
    """Everything a renderer of this exception could reach: the error
    itself and every cause and context behind it."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current)]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


def anthropic_failing(
    opening: BaseException | None = None,
    mid_stream: BaseException | None = None,
    final: BaseException | None = None,
) -> AnthropicLlm:
    """A provider whose stream fails where the argument says: `opening`
    for a request that never landed, `mid_stream` for a connection lost
    after a chunk was delivered, `final` for the assembled message the
    tool calls come from."""
    messages = FakeMessages(
        FakeStream(["Let me "], FakeMessage([FakeBlock(type="text")]), mid_stream, final),
        opening=opening,
    )
    return AnthropicLlm(
        model="claude-sonnet-5",
        max_tokens=64,
        api_key="sk-test",
        client=type("Client", (), {"messages": messages})(),  # type: ignore[arg-type]
    )


def openai_failing(
    opening: BaseException | None = None, mid_stream: BaseException | None = None
) -> OpenAiCompatibleLlm:
    completions = FakeCompletions(
        [FakeChunk([FakeChoice(FakeDelta(content="Let me "))])], opening, mid_stream
    )
    return OpenAiCompatibleLlm(
        base_url="http://localhost:11434/v1",
        model="qwen3:8b",
        max_tokens=64,
        api_key=None,
        client=type(  # type: ignore[arg-type]
            "Client", (), {"chat": type("Chat", (), {"completions": completions})()}
        )(),
    )


async def until_it_fails(llm: Any) -> tuple[list[Any], BaseException]:
    """What the stream delivered before it failed, and what it failed
    with. `pytest.raises` cannot say the first, and after the first
    chunk is exactly where these cases live."""
    events: list[Any] = []
    try:
        async for event in llm.stream("", [Turn("user", "hi")]):
            events.append(event)
    except BaseException as exc:  # noqa: B036 - the failure is the subject
        return events, exc
    raise AssertionError("the stream ended without failing")


async def test_anthropic_wraps_a_request_that_timed_out() -> None:
    _, failure = await until_it_fails(
        anthropic_failing(opening=anthropic.APITimeoutError(request=REQUEST))
    )
    assert isinstance(failure, ProviderCallTimeout)
    assert "APITimeoutError" in str(failure)


async def test_anthropic_wraps_an_api_error_and_keeps_the_vendors_text_out() -> None:
    """The class and the status are trusted metadata; the vendor's own
    sentence is not, because a message can carry the response body and
    a body can carry whatever was sent to produce it."""
    llm = anthropic_failing(opening=status_error(anthropic, 401, f"bad key {SENTINEL}"))
    _, failure = await until_it_fails(llm)

    assert isinstance(failure, ProviderCallError)
    assert not isinstance(failure, ProviderCallTimeout)
    assert "APIStatusError" in str(failure)
    assert "HTTP 401" in str(failure)
    assert SENTINEL not in chain(failure)


async def test_anthropic_passes_a_non_sdk_failure_through() -> None:
    """The taxonomy claims request failures, not all failures: a bug in
    this process must reach logger.exception as itself."""
    _, failure = await until_it_fails(anthropic_failing(opening=ValueError("a local bug")))
    assert type(failure) is ValueError
    assert str(failure) == "a local bug"


async def test_anthropic_wraps_a_timeout_after_the_first_chunk() -> None:
    llm = anthropic_failing(mid_stream=anthropic.APITimeoutError(request=REQUEST))
    events, failure = await until_it_fails(llm)

    assert events == [StreamStarted(), TextDelta("Let me ")]
    assert isinstance(failure, ProviderCallTimeout)


async def test_anthropic_wraps_a_raw_httpx_failure_after_the_response_opened() -> None:
    """The SDK rides httpx, so a transport error can escape the response
    iterator once the response has opened, wearing no SDK class at
    all."""
    events, failure = await until_it_fails(anthropic_failing(mid_stream=httpx.ReadError("gone")))
    assert events == [StreamStarted(), TextDelta("Let me ")]
    assert isinstance(failure, ProviderCallError)
    assert not isinstance(failure, ProviderCallTimeout)
    assert "ReadError" in str(failure)

    _, timed_out = await until_it_fails(anthropic_failing(mid_stream=httpx.ReadTimeout("slow")))
    assert isinstance(timed_out, ProviderCallTimeout)


async def test_anthropic_wraps_a_final_message_that_never_assembled() -> None:
    """Tool calls come from the assembled message, which is a second
    place this adapter waits on the wire."""
    llm = anthropic_failing(final=status_error(anthropic, 500, "upstream boom"))
    events, failure = await until_it_fails(llm)

    assert events == [StreamStarted(), TextDelta("Let me ")]
    assert isinstance(failure, ProviderCallError)
    assert "HTTP 500" in str(failure)


async def test_a_cancelled_anthropic_stream_is_not_a_provider_failure() -> None:
    """Barge-in cancels the reply mid-stream, and a cancellation dressed
    as a provider failure would be reported as one and, worse, swallowed
    by whoever handles them."""
    llm = anthropic_failing(mid_stream=asyncio.CancelledError())
    events, failure = await until_it_fails(llm)

    assert events == [StreamStarted(), TextDelta("Let me ")]
    assert isinstance(failure, asyncio.CancelledError)


async def test_a_failed_anthropic_request_leaks_nothing_into_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The planted secret sits in all three places one can hide: the
    SDK exception's message, the response body it carries, and a cause
    behind it. None of them may reach the raised chain, which is what
    the session renders into the retained log line."""
    error = status_error(anthropic, 400, f"rejected {SENTINEL}")
    error.__cause__ = ValueError(f"underneath: {SENTINEL}")

    with caplog.at_level("DEBUG"):
        _, failure = await until_it_fails(anthropic_failing(opening=error))

    assert SENTINEL not in chain(failure)
    assert SENTINEL not in caplog.text
    assert all(SENTINEL not in str(record.__dict__) for record in caplog.records)


async def test_openai_wraps_a_request_that_timed_out() -> None:
    _, failure = await until_it_fails(
        openai_failing(opening=openai.APITimeoutError(request=REQUEST))
    )
    assert isinstance(failure, ProviderCallTimeout)
    assert "APITimeoutError" in str(failure)


async def test_openai_wraps_an_api_error_and_keeps_the_vendors_text_out() -> None:
    llm = openai_failing(opening=status_error(openai, 429, f"quota gone {SENTINEL}"))
    _, failure = await until_it_fails(llm)

    assert isinstance(failure, ProviderCallError)
    assert not isinstance(failure, ProviderCallTimeout)
    assert "APIStatusError" in str(failure)
    assert "HTTP 429" in str(failure)
    assert SENTINEL not in chain(failure)


async def test_openai_passes_a_non_sdk_failure_through() -> None:
    _, failure = await until_it_fails(openai_failing(opening=ValueError("a local bug")))
    assert type(failure) is ValueError
    assert str(failure) == "a local bug"


async def test_openai_wraps_a_failure_after_the_first_chunk() -> None:
    events, timed_out = await until_it_fails(
        openai_failing(mid_stream=openai.APITimeoutError(request=REQUEST))
    )
    assert events == [StreamStarted(), TextDelta("Let me ")]
    assert isinstance(timed_out, ProviderCallTimeout)

    _, failure = await until_it_fails(
        openai_failing(mid_stream=status_error(openai, 500, "upstream boom"))
    )
    assert isinstance(failure, ProviderCallError)
    assert not isinstance(failure, ProviderCallTimeout)


async def test_openai_wraps_a_raw_httpx_failure_after_the_response_opened() -> None:
    _, failure = await until_it_fails(openai_failing(mid_stream=httpx.ReadError("gone")))
    assert isinstance(failure, ProviderCallError)
    assert "ReadError" in str(failure)


async def test_a_cancelled_openai_stream_is_not_a_provider_failure() -> None:
    events, failure = await until_it_fails(openai_failing(mid_stream=asyncio.CancelledError()))
    assert events == [StreamStarted(), TextDelta("Let me ")]
    assert isinstance(failure, asyncio.CancelledError)


async def test_a_failed_openai_request_leaks_nothing_into_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = status_error(openai, 400, f"rejected {SENTINEL}")
    error.__cause__ = ValueError(f"underneath: {SENTINEL}")

    with caplog.at_level("DEBUG"):
        _, failure = await until_it_fails(openai_failing(opening=error))

    assert SENTINEL not in chain(failure)
    assert SENTINEL not in caplog.text
    assert all(SENTINEL not in str(record.__dict__) for record in caplog.records)


async def test_anthropic_does_not_claim_the_other_vendors_failure() -> None:
    """An Anthropic entry holding an OpenAI client is a wiring bug, and
    dressing its errors as an Anthropic request failure would file that
    bug under "the API was slow" in the logs. Each provider catches its
    own SDK's families and httpx's, and nothing else."""
    _, failure = await until_it_fails(
        anthropic_failing(opening=status_error(openai, 500, "from the wrong sdk"))
    )
    assert isinstance(failure, openai.APIStatusError)
    assert not isinstance(failure, ProviderCallError)


async def test_openai_does_not_claim_the_other_vendors_failure() -> None:
    _, failure = await until_it_fails(
        openai_failing(opening=status_error(anthropic, 500, "from the wrong sdk"))
    )
    assert isinstance(failure, anthropic.APIStatusError)
    assert not isinstance(failure, ProviderCallError)
