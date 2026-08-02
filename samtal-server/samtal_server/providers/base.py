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
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class ProviderError(Exception):
    """A provider that cannot be built as configured: an unknown type,
    a bad option, or a missing optional dependency."""


@dataclass(frozen=True)
class Turn:
    """One conversation turn as the LLM stage sees it."""

    role: str  # "user" or "assistant"
    content: str


@runtime_checkable
class Endpointer(Protocol):
    """The per-session working end of the VAD stage: fed decoded PCM
    chunks while the device listens, it answers True the moment the
    utterance has ended."""

    def feed(self, pcm: bytes) -> bool: ...

    def reset(self) -> None: ...


class VadProvider(ABC):
    """Builds endpointers. One provider serves many sessions; each
    session gets its own endpointer, because endpointing is stateful."""

    @abstractmethod
    def new_endpointer(self) -> Endpointer: ...


class AsrProvider(ABC):
    """Speech to text, one whole utterance at a time."""

    @abstractmethod
    async def transcribe(self, pcm: bytes, sample_rate: int) -> str: ...


class LlmProvider(ABC):
    """Streams the reply to a conversation as text deltas."""

    @abstractmethod
    def stream(self, system: str, turns: Sequence[Turn]) -> AsyncIterator[str]: ...


class TtsProvider(ABC):
    """Text to speech, streamed as PCM chunks at `sample_rate`."""

    sample_rate: int

    @abstractmethod
    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...
