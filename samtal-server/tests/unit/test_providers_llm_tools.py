"""Tool calling on the wire, per provider.

The mapping between the neutral tool model and each API's shape is
pure-function territory and tested as such. Streaming is exercised
against a stub client rather than a live endpoint, because what matters
here is the request that goes out and the events that come back, not
the transport.
"""

import contextlib
from dataclasses import dataclass, field
from typing import Any

from samtal_server.providers import TextDelta, ToolCall, ToolDef, ToolResult, Turn
from samtal_server.providers.anthropic_llm import (
    AnthropicLlm,
    anthropic_messages,
    anthropic_tools,
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


@dataclass
class FakeBlock:
    type: str
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeMessage:
    content: list[FakeBlock]


class FakeStream:
    """The pieces of the SDK's streaming interface the provider uses."""

    def __init__(self, texts: list[str], message: FakeMessage) -> None:
        self._texts = texts
        self._message = message

    @property
    async def text_stream(self):
        for text in self._texts:
            yield text

    async def get_final_message(self) -> FakeMessage:
        return self._message


class FakeMessages:
    def __init__(self, stream: FakeStream) -> None:
        self._stream = stream
        self.request: dict[str, Any] = {}

    def stream(self, **request: Any):
        self.request = request

        @contextlib.asynccontextmanager
        async def opened():
            yield self._stream

        return opened()


def anthropic_with(texts: list[str], blocks: list[FakeBlock]) -> tuple[AnthropicLlm, FakeMessages]:
    llm = AnthropicLlm(model="claude-sonnet-5", max_tokens=64, api_key="sk-test")
    messages = FakeMessages(FakeStream(texts, FakeMessage(blocks)))
    llm._client = type("Client", (), {"messages": messages})()  # type: ignore[assignment]
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
        TextDelta("Let me "),
        TextDelta("check."),
        ToolCall(id="t1", name="weather__forecast", arguments={"city": "Lund"}),
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
    assert events == [TextDelta("Fine.")]
    # The definitions still go out, so the conversation the model sees
    # does not change shape between rounds; only calling is forbidden.
    assert messages.request["tools"]
    assert messages.request["tool_choice"] == {"type": "none"}


async def test_anthropic_sends_no_tool_fields_when_there_are_no_tools() -> None:
    llm, messages = anthropic_with(["Hello."], [FakeBlock(type="text")])
    [event async for event in llm.stream("", [Turn("user", "hi")])]
    assert "tools" not in messages.request
    assert "tool_choice" not in messages.request
