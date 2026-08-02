"""Local speech recognition on faster-whisper (CTranslate2).

The optional `faster-whisper` extra provides this module's engine:
`uv sync --extra faster-whisper`. Model weights download from Hugging
Face into the local cache when the provider is built, which is server
startup, so the first conversation does not pay for the download.

Whisper is multilingual; the optional `language` hint pins one language
instead of detecting it per utterance, which is both faster and more
robust for short commands.
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
    ) -> None:
        logger.info("loading faster-whisper model %s (%s, %s)", model, device, compute_type)
        self._model = WhisperModel(
            model, device=device, compute_type=compute_type, download_root=download_dir
        )
        self._language = language
        self._beam_size = beam_size

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        if sample_rate != EXPECTED_SAMPLE_RATE:
            raise ValueError(f"faster-whisper is fed {EXPECTED_SAMPLE_RATE} Hz, got {sample_rate}")
        return await asyncio.to_thread(self._transcribe, pcm)

    def _transcribe(self, pcm: bytes) -> str:
        segments, _info = self._model.transcribe(
            pcm_to_float(pcm), language=self._language, beam_size=self._beam_size
        )
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
        beam_size=options.integer("beam_size", 5),
        download_dir=options.string("download_dir"),
    )
    options.finish()
    return provider
