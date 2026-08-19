"""Local text to speech on Piper.

The optional `piper` extra provides the engine: `uv sync --extra
piper`. piper-tts (the maintained piper1-gpl) is GPL-3.0, which is why
it is an extra and never a core dependency. Voices come from the Piper
voice collection on Hugging Face, downloaded into `download_dir` when
the provider is built, at server startup; the voice's native sample
rate (22.05 kHz for the medium voices) is announced through the
provider and resampled by the session.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from piper import PiperVoice
from piper.download_voices import download_voice

from vinga_server.config.models import ProviderConfig
from vinga_server.providers.base import TtsProvider
from vinga_server.providers.registry import OptionsReader

logger = logging.getLogger(__name__)

DEFAULT_DOWNLOAD_DIR = Path.home() / ".cache" / "vinga" / "piper"


def ensure_voice(voice: str, download_dir: Path) -> Path:
    """The voice's onnx path, downloading model and config if either is
    missing. Piper names the config `<voice>.onnx.json`."""
    onnx = download_dir / f"{voice}.onnx"
    if not (onnx.exists() and Path(f"{onnx}.json").exists()):
        logger.info("downloading piper voice %s to %s", voice, download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        download_voice(voice, download_dir)
    return onnx


class PiperTts(TtsProvider):
    # Synthesis runs on the host; only the voice downloads, at startup.
    egress = False

    def __init__(self, voice: str, download_dir: Path) -> None:
        self._voice = PiperVoice.load(ensure_voice(voice, download_dir))
        self.sample_rate = self._voice.config.sample_rate

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        for chunk in await asyncio.to_thread(self._synthesize, text):
            yield chunk

    def _synthesize(self, text: str) -> list[bytes]:
        return [chunk.audio_int16_bytes for chunk in self._voice.synthesize(text)]


def build(label: str, config: ProviderConfig) -> PiperTts:
    options = OptionsReader(label, config)
    voice = options.required_string("voice")
    download_dir = options.string("download_dir")
    options.finish()
    return PiperTts(
        voice=voice,
        download_dir=Path(download_dir) if download_dir else DEFAULT_DOWNLOAD_DIR,
    )
