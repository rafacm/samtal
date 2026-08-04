"""Local speech recognition on faster-whisper (CTranslate2).

The optional `faster-whisper` extra provides this module's engine:
`uv sync --extra faster-whisper`. Model weights download from Hugging
Face into the local cache when the provider is built, which is server
startup, so the first conversation does not pay for the download.

Whisper is multilingual; the optional `language` hint pins one language
instead of detecting it per utterance, which is both faster and more
robust for short commands.

The decode options mirror `WhisperModel.transcribe` arguments of the
same name and keep the engine's defaults when unset, with one
exception: `beam_size` defaults to greedy decoding (1), because beam
search buys little accuracy on short spoken commands and costs a
multiple of the CPU time (#19).
"""

import asyncio
import logging

import numpy as np
from faster_whisper import WhisperModel

from samtal_server.config.models import ProviderConfig
from samtal_server.providers.base import AsrProvider
from samtal_server.providers.registry import OptionsReader

logger = logging.getLogger(__name__)

# What faster-whisper expects, and what the pipeline feeds it.
EXPECTED_SAMPLE_RATE = 16000


def pcm_to_float(pcm: bytes) -> "np.ndarray":
    """s16le bytes to the float32 array faster-whisper consumes."""
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


class FasterWhisperAsr(AsrProvider):
    def __init__(
        self,
        model: str,
        language: str | None,
        device: str,
        compute_type: str,
        beam_size: int,
        download_dir: str | None,
        cpu_threads: int,
        vad_filter: bool,
        vad_parameters: dict[str, object],
        condition_on_previous_text: bool,
        temperature: list[float] | None,
    ) -> None:
        logger.info("loading faster-whisper model %s (%s, %s)", model, device, compute_type)
        self._model = WhisperModel(
            model,
            device=device,
            compute_type=compute_type,
            download_root=download_dir,
            cpu_threads=cpu_threads,
        )
        # Everything transcribe() is called with. Options with no entry
        # here keep the engine's default for the installed version.
        self._decode_options: dict[str, object] = {
            "language": language,
            "beam_size": beam_size,
            "vad_filter": vad_filter,
            "condition_on_previous_text": condition_on_previous_text,
        }
        if vad_parameters:
            self._decode_options["vad_parameters"] = vad_parameters
        if temperature is not None:
            self._decode_options["temperature"] = temperature

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        if sample_rate != EXPECTED_SAMPLE_RATE:
            raise ValueError(f"faster-whisper is fed {EXPECTED_SAMPLE_RATE} Hz, got {sample_rate}")
        return await asyncio.to_thread(self._transcribe, pcm)

    def _transcribe(self, pcm: bytes) -> str:
        segments, _info = self._model.transcribe(pcm_to_float(pcm), **self._decode_options)
        # segments is a generator; consuming it here keeps the decoding
        # inside the worker thread rather than the event loop.
        return " ".join(segment.text.strip() for segment in segments).strip()


def build(label: str, config: ProviderConfig) -> FasterWhisperAsr:
    options = OptionsReader(label, config)
    provider = FasterWhisperAsr(
        model=options.string("model", "small") or "small",
        language=options.string("language"),
        device=options.string("device", "cpu") or "cpu",
        compute_type=options.string("compute_type", "int8") or "int8",
        beam_size=options.integer("beam_size", 1),
        download_dir=options.string("download_dir"),
        cpu_threads=options.integer("cpu_threads", 0),
        vad_filter=options.boolean("vad_filter", False),
        vad_parameters=options.mapping("vad_parameters"),
        condition_on_previous_text=options.boolean("condition_on_previous_text", True),
        temperature=options.numbers("temperature"),
    )
    options.finish()
    return provider
