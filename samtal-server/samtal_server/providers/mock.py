"""Deterministic providers for tests and CI.

Keyless, network-free, and model-free: the integration lane runs the
whole pipeline on these. The VAD is the energy endpointer (a real VAD
would rightly refuse to call the test tones speech), the ASR answers a
configured transcript, the LLM formats it into a reply, and the TTS
speaks a tone whose length follows the text.
"""

import math
import struct
from collections.abc import AsyncIterator, Sequence

from samtal_server.audio.endpointing import EnergyEndpointer
from samtal_server.config.models import ProviderConfig
from samtal_server.providers.base import (
    AsrProvider,
    Endpointer,
    LlmProvider,
    TtsProvider,
    Turn,
    VadProvider,
)
from samtal_server.providers.registry import OptionsReader

TONE_HZ = 440.0
TONE_AMPLITUDE = 8000
CHUNK_MS = 20


class MockVad(VadProvider):
    """Energy endpointing with the M3 thresholds, as a provider."""

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

    def __init__(self, text: str) -> None:
        self._text = text

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        if not pcm:
            return ""
        duration_ms = len(pcm) // 2 * 1000 // sample_rate
        return self._text.replace("{ms}", str(duration_ms))


class MockLlm(LlmProvider):
    """Formats the last user turn into the configured reply template,
    streamed word by word so sentence assembly is exercised."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def stream(self, system: str, turns: Sequence[Turn]) -> AsyncIterator[str]:
        last_user = next((turn.content for turn in reversed(turns) if turn.role == "user"), "")
        reply = self._reply.format(text=last_user)
        for index, word in enumerate(reply.split(" ")):
            yield word if index == 0 else " " + word


class MockTts(TtsProvider):
    """Speaks a fixed tone; the duration follows the text length, so a
    test can tell replies apart by ear (or by sample count)."""

    def __init__(self, sample_rate: int, ms_per_char: float, min_ms: float) -> None:
        self.sample_rate = sample_rate
        self._ms_per_char = ms_per_char
        self._min_ms = min_ms

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
                        * math.sin(2 * math.pi * TONE_HZ * (start + n) / self.sample_rate)
                    ),
                )
                for n in range(count)
            )


def build_vad(label: str, config: ProviderConfig) -> MockVad:
    options = OptionsReader(label, config)
    provider = MockVad(
        threshold=options.number("threshold", 500.0),
        trailing_silence_ms=options.number("trailing_silence_ms", 700.0),
        max_utterance_ms=options.number("max_utterance_ms", 10_000.0),
    )
    options.finish()
    return provider


def build_asr(label: str, config: ProviderConfig) -> MockAsr:
    options = OptionsReader(label, config)
    provider = MockAsr(text=options.string("text", "hello") or "")
    options.finish()
    return provider


def build_llm(label: str, config: ProviderConfig) -> MockLlm:
    options = OptionsReader(label, config)
    provider = MockLlm(reply=options.string("reply", "You said {text}.") or "")
    options.finish()
    return provider


def build_tts(label: str, config: ProviderConfig) -> MockTts:
    options = OptionsReader(label, config)
    provider = MockTts(
        sample_rate=options.integer("sample_rate", 24_000),
        ms_per_char=options.number("ms_per_char", 40.0),
        min_ms=options.number("min_ms", 240.0),
    )
    options.finish()
    return provider
