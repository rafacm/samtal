"""Fakes shaped like the SDKs the two LLM dialects stream through.

What belongs here is a double for an object the vendor SDK hands the
provider, not a double for anything vinga owns: a stream, a chunk, a
delta, the completions endpoint the provider calls. The providers take
their client by injection, so a test can drive a whole stream without a
network, a key, or a mock library, and these classes are the shapes
that client returns.

They live in support rather than in one provider's test module because
they describe the vendor dialects, not one suite's cases: the anthropic
messages-stream family and the openai chat-completions family are what
any future test of either dialect needs, and copying them into a second
module is how two descriptions of one wire protocol start to drift.

Each class carries only the attributes the provider under test reads,
so a shape that grows a field the provider ignores does not grow here.
"""

import contextlib
from dataclasses import dataclass, field
from typing import Any

# --- the anthropic messages-stream dialect ---------------------------


@dataclass
class FakeBlock:
    type: str
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeUsage:
    input_tokens: int = 11
    output_tokens: int = 7


@dataclass
class FakeMessage:
    content: list[FakeBlock]
    usage: FakeUsage = field(default_factory=FakeUsage)


@dataclass
class FakeTextDelta:
    text: str
    type: str = "text_delta"


@dataclass
class FakeStreamEvent:
    type: str
    delta: FakeTextDelta | None = None


class FakeStream:
    """The pieces of the SDK's streaming interface the provider uses:
    iterating yields the stream's events, opening with the
    message_start every real stream begins with.

    `mid_stream` is raised once the events have been delivered and
    `final` when the assembled message is asked for, which are the two
    places a real stream can fail after the request itself landed."""

    def __init__(
        self,
        texts: list[str],
        message: FakeMessage,
        mid_stream: BaseException | None = None,
        final: BaseException | None = None,
    ) -> None:
        self._texts = texts
        self._message = message
        self._mid_stream = mid_stream
        self._final = final

    def __aiter__(self):
        return self._events()

    async def _events(self):
        yield FakeStreamEvent(type="message_start")
        for text in self._texts:
            yield FakeStreamEvent(type="content_block_delta", delta=FakeTextDelta(text))
        if self._mid_stream is not None:
            raise self._mid_stream

    async def get_final_message(self) -> FakeMessage:
        if self._final is not None:
            raise self._final
        return self._message


class FakeMessages:
    """`opening` is raised where the SDK sends the request, which is on
    entering the context manager rather than on building it."""

    def __init__(self, stream: FakeStream, opening: BaseException | None = None) -> None:
        self._stream = stream
        self._opening = opening
        self.request: dict[str, Any] = {}

    def stream(self, **request: Any):
        self.request = request

        @contextlib.asynccontextmanager
        async def opened():
            if self._opening is not None:
                raise self._opening
            yield self._stream

        return opened()


# --- the openai chat-completions dialect -----------------------------


@dataclass
class FakeFunction:
    name: str | None = None
    arguments: str | None = None


@dataclass
class FakeFragment:
    index: int
    id: str | None = None
    function: FakeFunction | None = None


@dataclass
class FakeDelta:
    content: str | None = None
    tool_calls: list[FakeFragment] | None = None


@dataclass
class FakeChoice:
    delta: FakeDelta


@dataclass
class FakeChunkUsage:
    prompt_tokens: int
    completion_tokens: int


@dataclass
class FakeChunk:
    choices: list[FakeChoice]
    usage: FakeChunkUsage | None = None


class FakeCompletions:
    """`opening` is raised where the request is sent and `mid_stream`
    once the chunks have been delivered, which are the two places this
    dialect's stream can fail."""

    def __init__(
        self,
        chunks: list[FakeChunk],
        opening: BaseException | None = None,
        mid_stream: BaseException | None = None,
    ) -> None:
        self._chunks = chunks
        self._opening = opening
        self._mid_stream = mid_stream
        self.request: dict[str, Any] = {}

    async def create(self, **request: Any):
        self.request = request
        if self._opening is not None:
            raise self._opening

        async def streamed():
            for chunk in self._chunks:
                yield chunk
            if self._mid_stream is not None:
                raise self._mid_stream

        return streamed()


# --- the injected-client probes --------------------------------------


class Falsey:
    """A double that answers False to a truth test, which is what any
    object defining __bool__ or __len__ does. `client or ...` drops one
    of these on the floor and builds a real client instead, and the test
    that thought it had injected a client watches the provider talk to
    the vendor."""

    def __bool__(self) -> bool:
        return False
