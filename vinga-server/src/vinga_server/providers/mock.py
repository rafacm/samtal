"""Deterministic providers for tests and CI.

Keyless, network-free, and model-free: the integration lane runs the
whole pipeline on these. The VAD is the energy endpointer (a real VAD
would rightly refuse to call the test tones speech), the ASR answers a
configured transcript, the LLM formats it into a reply, and the TTS
speaks a tone whose length follows the text.
"""

import math
import re
import struct
from collections.abc import AsyncIterator, Sequence

from vinga_server.audio import rms
from vinga_server.config.models import ProviderConfig
from vinga_server.providers.base import (
    AsrProvider,
    AsrResult,
    Endpointer,
    LlmEvent,
    LlmProvider,
    StreamStarted,
    TextDelta,
    ToolCall,
    ToolChoice,
    ToolDef,
    TtsProvider,
    Turn,
    VadProvider,
)
from vinga_server.providers.registry import OptionsReader

TONE_HZ = 440.0
TONE_AMPLITUDE = 8000
CHUNK_MS = 20


class EnergyEndpointer:
    """End-of-utterance detection by signal energy: the utterance has
    ended once speech has been heard and the signal then stays below an
    RMS threshold for a trailing-silence window, or once it has simply
    run long enough. This was the M3 stand-in for a real VAD; it lives
    on as the mock because it is deterministic on synthetic audio."""

    def __init__(
        self,
        sample_rate: int = 16000,
        threshold: float = 500.0,
        trailing_silence_ms: float = 700.0,
        max_utterance_ms: float = 10_000.0,
    ) -> None:
        self._sample_rate = sample_rate
        self._threshold = threshold
        self._trailing_silence_ms = trailing_silence_ms
        self._max_utterance_ms = max_utterance_ms
        self.reset()

    def reset(self) -> None:
        self._speech_heard = False
        self._silence_ms = 0.0
        self._utterance_ms = 0.0
        self._speech_ms = 0.0
        self._fed_bytes = 0
        self._speech_start: int | None = None

    def speech_start(self) -> int | None:
        return self._speech_start

    def speech_ms(self) -> float:
        return self._speech_ms

    def feed(self, pcm: bytes) -> bool:
        """Account one chunk; True when the utterance just ended. Silence
        before any speech counts toward nothing, so a device left
        listening in a quiet room never trips this."""
        duration_ms = len(pcm) / 2 / self._sample_rate * 1000
        if rms(pcm) >= self._threshold:
            if not self._speech_heard:
                # The start of this chunk, to fed-chunk granularity.
                self._speech_start = self._fed_bytes
            self._speech_heard = True
            self._speech_ms += duration_ms
            self._silence_ms = 0.0
        elif self._speech_heard:
            self._silence_ms += duration_ms
        self._fed_bytes += len(pcm)
        if not self._speech_heard:
            return False
        self._utterance_ms += duration_ms
        return (
            self._silence_ms >= self._trailing_silence_ms
            or self._utterance_ms >= self._max_utterance_ms
        )


class MockVad(VadProvider):
    """Energy endpointing with the M3 thresholds, as a provider."""

    # The mocks are network-free by construction.
    egress = False

    def __init__(
        self, threshold: float, trailing_silence_ms: float, max_utterance_ms: float
    ) -> None:
        self._threshold = threshold
        self._trailing_silence_ms = trailing_silence_ms
        self._max_utterance_ms = max_utterance_ms

    def new_endpointer(self) -> Endpointer:
        return EnergyEndpointer(
            threshold=self._threshold,
            trailing_silence_ms=self._trailing_silence_ms,
            max_utterance_ms=self._max_utterance_ms,
        )


class MockAsr(AsrProvider):
    """Answers the configured transcript for any non-empty utterance.
    An `{ms}` in the text becomes the utterance duration, so a test can
    see how much audio actually reached the pipeline."""

    egress = False

    def __init__(self, text: str) -> None:
        self._text = text

    async def transcribe(
        self, pcm: bytes, sample_rate: int, language_hint: str | None = None
    ) -> AsrResult:
        if not pcm:
            return AsrResult(text="")
        duration_ms = len(pcm) // 2 * 1000 // sample_rate
        return AsrResult(text=self._text.replace("{ms}", str(duration_ms)))


class MockLlm(LlmProvider):
    """Formats the last user turn into the configured reply template,
    streamed word by word so sentence assembly is exercised. The template
    takes `{text}` (the last user turn), `{system}` (the prompt the
    session handed over, so a test can prove a reply came from one
    agent's own prompt and not another's), `{tools}` (the names this
    reply was offered, the same trick `{system}` plays for the prompt:
    what a session gave the model is otherwise invisible from outside,
    so a test about which tools an agent may reach could only watch
    which calls happened, and a forbidden tool nobody called would pass)
    and `{tool_result}` (whatever the tools answered this reply).

    Tool calling is scripted rather than decided: when `tool_when` is a
    substring of the last user turn, the first round asks for
    `tool_name` with `tool_arguments` and says nothing, and the round
    after the results come back speaks the template. That makes the
    whole loop deterministic, which is what the acceptance test needs.
    Without the tool options this behaves exactly as it did before.

    Two options extend the script to the flows where one call answers
    the next, which a template cannot express because the argument is a
    value only the previous result holds.

    `then_pattern` and `then_arguments` are the second beat: once the
    results are back, a first capturing group matched against them is
    substituted for `{found}` in each argument, and the same tool is
    asked for again. That is how a two-argument tool (search, then pick
    what the search answered) is driven end to end without teaching this
    double to read.

    `tool_unless` is the brake: no call at all where that text appears
    anywhere in the turns this model was handed. A script keyed on what
    it has already seen is what makes the utterance after a successful
    flow an ordinary one, in a lane where every utterance transcribes
    the same."""

    egress = False

    def __init__(
        self,
        reply: str,
        tool_when: str | None = None,
        tool_name: str = "",
        tool_arguments: dict[str, object] | None = None,
        tool_unless: str | None = None,
        then_pattern: str | None = None,
        then_arguments: dict[str, object] | None = None,
    ) -> None:
        self._reply = reply
        self._tool_when = tool_when
        self._tool_name = tool_name
        self._tool_arguments = tool_arguments or {}
        self._tool_unless = tool_unless
        self._then_pattern = then_pattern
        self._then_arguments = then_arguments or {}
        self._calls = 0

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        # What the real adapters yield first: proof the wire is live,
        # so the integration lane exercises the same event flow the
        # session sees from a cloud provider.
        yield StreamStarted()
        last_user = next((turn.content for turn in reversed(turns) if turn.role == "user"), "")
        results = [result for turn in turns for result in turn.tool_results]
        answered = " ".join(result.content for result in results)

        if self._seen(turns) or tool_choice == "none":
            arguments = None
        elif results:
            arguments = self._then(answered)
        elif self._tool_when is not None and self._tool_when in last_user:
            arguments = dict(self._tool_arguments)
        else:
            arguments = None
        if arguments is not None:
            self._calls += 1
            yield ToolCall(
                id=f"call_{self._calls}", name=self._tool_name, arguments=arguments
            )
            return

        reply = self._reply.format(
            text=last_user,
            system=system,
            tools=", ".join(tool.name for tool in tools),
            tool_result=answered,
        )
        for index, word in enumerate(reply.split(" ")):
            yield TextDelta(word if index == 0 else " " + word)

    def _seen(self, turns: Sequence[Turn]) -> bool:
        """Whether what this model has been handed already says the
        scripted flow has run, in which case it asks for nothing and
        speaks."""
        if self._tool_unless is None:
            return False
        return any(self._tool_unless in turn.content for turn in turns)

    def _then(self, answered: str) -> dict[str, object] | None:
        """The call that answers the previous one, or None where there
        is no second beat scripted or nothing in the results to make one
        out of. Without it a round holding results always speaks, which
        is what keeps the scripted loop terminating."""
        if self._then_pattern is None:
            return None
        found = re.search(self._then_pattern, answered)
        if found is None:
            return None
        return {
            key: value.replace("{found}", found.group(1))
            if isinstance(value, str)
            else value
            for key, value in self._then_arguments.items()
        }


class MockTts(TtsProvider):
    """Speaks a fixed tone; the duration follows the text length, so a
    test can tell replies apart by ear (or by sample count). The tone
    frequency is an option, which is how two mock "voices" are told
    apart in received audio."""

    egress = False

    def __init__(
        self, sample_rate: int, ms_per_char: float, min_ms: float, tone_hz: float = TONE_HZ
    ) -> None:
        self.sample_rate = sample_rate
        self._ms_per_char = ms_per_char
        self._min_ms = min_ms
        self._tone_hz = tone_hz

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        duration_ms = max(self._min_ms, self._ms_per_char * len(text))
        samples = int(self.sample_rate * duration_ms / 1000)
        chunk_samples = self.sample_rate * CHUNK_MS // 1000
        for start in range(0, samples, chunk_samples):
            count = min(chunk_samples, samples - start)
            yield b"".join(
                struct.pack(
                    "<h",
                    int(
                        TONE_AMPLITUDE
                        * math.sin(2 * math.pi * self._tone_hz * (start + n) / self.sample_rate)
                    ),
                )
                for n in range(count)
            )


def build_vad(label: str, config: ProviderConfig) -> MockVad:
    options = OptionsReader(label, config)
    threshold = options.number("threshold", 500.0)
    trailing_silence_ms = options.number("trailing_silence_ms", 700.0)
    max_utterance_ms = options.number("max_utterance_ms", 10_000.0)
    options.finish()
    return MockVad(
        threshold=threshold,
        trailing_silence_ms=trailing_silence_ms,
        max_utterance_ms=max_utterance_ms,
    )


def build_asr(label: str, config: ProviderConfig) -> MockAsr:
    options = OptionsReader(label, config)
    text = options.string("text", "hello") or ""
    options.finish()
    return MockAsr(text=text)


def build_llm(label: str, config: ProviderConfig) -> MockLlm:
    options = OptionsReader(label, config)
    reply = options.string("reply", "You said {text}.") or ""
    tool_when = options.string("tool_when")
    tool_name = options.string("tool_name", "") or ""
    tool_arguments = options.mapping("tool_arguments")
    tool_unless = options.string("tool_unless")
    then_pattern = options.string("then_pattern")
    then_arguments = options.mapping("then_arguments")
    options.finish()
    return MockLlm(
        reply=reply,
        tool_when=tool_when,
        tool_name=tool_name,
        tool_arguments=tool_arguments,
        tool_unless=tool_unless,
        then_pattern=then_pattern,
        then_arguments=then_arguments,
    )


def build_tts(label: str, config: ProviderConfig) -> MockTts:
    options = OptionsReader(label, config)
    sample_rate = options.integer("sample_rate", 24_000)
    ms_per_char = options.number("ms_per_char", 40.0)
    min_ms = options.number("min_ms", 240.0)
    tone_hz = options.number("tone_hz", TONE_HZ)
    options.finish()
    return MockTts(
        sample_rate=sample_rate,
        ms_per_char=ms_per_char,
        min_ms=min_ms,
        tone_hz=tone_hz,
    )
