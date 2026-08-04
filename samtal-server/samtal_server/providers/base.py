"""The provider interfaces behind the conversation pipeline.

One small interface per stage: VAD segments speech, ASR transcribes,
the LLM streams a reply, TTS streams audio. Implementations register a
type name in `samtal_server.providers.registry` and are configured
through the named entries under `providers` in the YAML configuration;
which implementation serves a session is the agent's choice.

All PCM crossing these interfaces is s16le mono: endpointers are fed
the pipeline rate of 16 kHz, ASR is told the rate per call, and TTS
announces the rate it produces.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


class ProviderError(Exception):
    """A provider that cannot be built as configured: an unknown type,
    a bad option, or a missing optional dependency."""


@dataclass(frozen=True)
class ToolDef:
    """One tool as the model is told about it. The schema is JSON Schema,
    which is what MCP speaks on both sides of this server, so nothing has
    to be translated on the way in."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """The model asking for a tool, as the session receives it.

    `malformed_arguments` holds the raw argument text when a model
    streamed something that is not a JSON object. The call still reaches
    the session, which answers it with an error result: a model that
    mangles its own arguments should be told so and get another round,
    not crash the reply."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    malformed_arguments: str | None = None


@dataclass(frozen=True)
class ToolResult:
    """What a tool answered, as the model is told about it. Failures are
    results too: the model phrases what to tell the user, in its own
    voice and the user's language."""

    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class TextDelta:
    """A piece of the spoken reply, as it streams."""

    text: str


# What an LLM stream yields: speech, or a request to run a tool.
LlmEvent = TextDelta | ToolCall

# Whether the model may call the tools it was given. "none" still sends
# the definitions (so the conversation stays consistent) while forbidding
# a call, which is how the session guarantees a reply ends in speech.
ToolChoice = Literal["auto", "none"]


@dataclass(frozen=True)
class Turn:
    """One conversation turn as the LLM stage sees it.

    The two tool fields are empty for everything the session keeps:
    persistent history is plain text, and the structured turns exist
    only in the working copy inside one reply. An assistant turn that
    asked for tools carries `tool_calls`; the turn answering them has
    role "tool" and carries `tool_results`."""

    role: str  # "user", "assistant", or "tool"
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()


@runtime_checkable
class Endpointer(Protocol):
    """The per-session working end of the VAD stage: fed decoded PCM
    chunks while the device listens, it answers True the moment the
    utterance has ended.

    `speech_start` is where in the fed stream the speech began: a byte
    offset counted from the last reset, None while none has been heard.
    It exists because only the endpointer knows, and the session needs
    it to drop the leading silence a continuously listening device
    piles up in front of every utterance (#14)."""

    def feed(self, pcm: bytes) -> bool: ...

    def reset(self) -> None: ...

    def speech_start(self) -> int | None: ...


class VadProvider(ABC):
    """Builds endpointers. One provider serves many sessions; each
    session gets its own endpointer, because endpointing is stateful."""

    @abstractmethod
    def new_endpointer(self) -> Endpointer: ...


@dataclass(frozen=True)
class AsrResult:
    """A transcription, and what the engine learned getting it.

    `language` and `language_confidence` are what detection concluded,
    None when the engine did not detect (pinned, hinted, or an engine
    that has no notion of language). `lock_language` is the provider
    asking the session to reuse a language for the rest of the session:
    the session hands it back as `language_hint` on later calls, which
    is what lets a per-session policy live in a provider that is itself
    shared between sessions and holds no per-session state."""

    text: str
    language: str | None = None
    language_confidence: float | None = None
    lock_language: str | None = None


class AsrProvider(ABC):
    """Speech to text, one whole utterance at a time.

    `language_hint` is a session-scoped suggestion, usually a
    `lock_language` this provider returned earlier in the same session;
    a provider is free to ignore it, and a configured language always
    beats it."""

    @abstractmethod
    async def transcribe(
        self, pcm: bytes, sample_rate: int, language_hint: str | None = None
    ) -> AsrResult: ...


class LlmProvider(ABC):
    """Streams the reply to a conversation as speech and tool requests.

    Providers stay translators: they map the neutral model above onto
    one API's wire shape and back. The tool loop itself (executing
    calls, feeding results back, capping the rounds) belongs to the
    session, which is the only place that can switch agents between
    rounds."""

    @abstractmethod
    def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]: ...


class TtsProvider(ABC):
    """Text to speech, streamed as PCM chunks at `sample_rate`."""

    sample_rate: int

    @abstractmethod
    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...
