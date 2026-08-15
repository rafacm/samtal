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
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable


class ProviderError(Exception):
    """A provider that cannot be built as configured: an unknown type,
    a bad option, or a missing optional dependency."""


@dataclass(frozen=True)
class ProviderIdentity:
    """Which configuration entry a provider is, as an operator reading
    the logs knows it.

    A session holds provider objects, not the YAML they came from, so
    without this a failing provider can only be described as "the TTS
    one". The registry stamps it at build time, where the stage, the
    entry name and the type are all in hand.

    `host` is the one field the provider itself has to supply, since
    only it knows whether its `base_url` points at a vendor or at
    localhost, and it is the actionable half for anyone with an egress
    allowlist: it turns "TTS is broken" into "TTS cannot reach
    api.elevenlabs.io". It is None for an engine that runs in this
    process and reaches nothing."""

    stage: str
    name: str
    type: str
    host: str | None = None


class Provider:
    """What every stage's provider type has in common: the egress
    marking, the host it reaches, and the identity it is stamped with.

    `egress` declares whether providers of this type send session data
    (audio, transcripts, replies) off the host. True marks a cloud
    provider, False one that keeps everything on the machine, and None a
    type whose configuration decides (an openai_compatible base_url can
    name localhost or a vendor), which under `server.local_only` demands
    an explicit `egress` declaration on the provider entry (#30).

    There is no default. Every concrete type declares its own marking in
    its own class body, and one that declared none, or declared
    something that is not one of the three, is refused when it is built,
    in any mode (`samtal_server.egress`). Inheriting a parent's marking
    does not count either: a subclass of a cloud provider says so
    itself, so the answer is always written where the type is (#136).
    The abstract stage bases below stay undeclared, since nothing builds
    them.

    `host` is what this entry talks to, set by providers that talk to
    anything, and it is a fact about the built entry rather than about
    the type: two `openai` entries can reach different hosts.

    `identity` is None until `build_provider` stamps it, which is every
    provider a running server holds. A hand-built one (a test, a
    fixture) keeps None, and the events that describe it simply carry
    fewer fields rather than inventing any."""

    egress: ClassVar[bool | None]
    host: str | None = None
    identity: ProviderIdentity | None = None


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
class StreamStarted:
    """The stream's first raw chunk arrived from the wire, whatever it
    held.

    It exists for the session's first-token watchdog. Both adapters
    buffer tool-call fragments until their stream has ended, so a round
    that streams only a tool call yields no other event while healthily
    delivering, and without this signal the watchdog could not tell
    that round from a request the provider never answered (#68).

    Yielded at most once, before anything else, and it carries nothing:
    it is evidence of liveness, not content. The session consumes it in
    the watchdog and it reaches nothing downstream; consumers of the
    stream must nevertheless tolerate and ignore it."""


@dataclass(frozen=True)
class TextDelta:
    """A piece of the spoken reply, as it streams."""

    text: str


@dataclass(frozen=True)
class Usage:
    """What one generation cost, as the provider reported it.

    Yielded at most once per stream, and only when the API says: it is
    what tells a slow round caused by a growing payload from a slow
    round caused by the vendor (#55). A provider that is never told
    yields nothing, which is a fact about the endpoint rather than a
    failure, so the session's event carries the fields it has.

    Counts, never content: tokens are a size, and the ADR on logging
    keeps the text of a conversation out of everything but the events
    that exist to carry it."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


# What an LLM stream yields: proof the wire is live, speech, a request
# to run a tool, or what the generation cost.
LlmEvent = StreamStarted | TextDelta | ToolCall | Usage

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
    piles up in front of every utterance (#14).

    `speech_ms` is how much of the fed stream was classified as speech
    since the last reset, in milliseconds at the implementation's own
    window granularity. It exists for the same reason: only the
    endpointer can tell a sustained interjection from a noise blip, and
    the session's barge-in gates need that distinction (#28)."""

    def feed(self, pcm: bytes) -> bool: ...

    def reset(self) -> None: ...

    def speech_start(self) -> int | None: ...

    def speech_ms(self) -> float: ...


class VadProvider(Provider, ABC):
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


class AsrProvider(Provider, ABC):
    """Speech to text, one whole utterance at a time.

    `language_hint` is a session-scoped suggestion, usually a
    `lock_language` this provider returned earlier in the same session;
    a provider is free to ignore it, and a configured language always
    beats it."""

    @abstractmethod
    async def transcribe(
        self, pcm: bytes, sample_rate: int, language_hint: str | None = None
    ) -> AsrResult: ...


class LlmProvider(Provider, ABC):
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


class TtsProvider(Provider, ABC):
    """Text to speech, streamed as PCM chunks at `sample_rate`."""

    sample_rate: int

    @abstractmethod
    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...
