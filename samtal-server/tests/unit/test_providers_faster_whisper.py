"""The faster-whisper provider, as far as a unit test can go.

Transcription with a real model needs weights from the network, which
the test lanes never download; that path is exercised by the local lane
and the device checkpoint. What runs here depends on whether the extra
is installed: the PCM conversion when it is, the helpful registry error
when it is not.
"""

import importlib.util

import pytest

from samtal_server.config.models import ProviderConfig
from samtal_server.providers import ProviderError, build_provider

HAS_FASTER_WHISPER = importlib.util.find_spec("faster_whisper") is not None


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster-whisper extra not installed")
def test_pcm_converts_to_normalized_float32() -> None:
    from samtal_server.providers.faster_whisper import pcm_to_float

    audio = pcm_to_float(b"\x00\x00\xff\x7f\x00\x80")  # 0, 32767, -32768
    assert audio.dtype.name == "float32"
    assert audio[0] == 0.0
    assert 0.99 < audio[1] <= 1.0
    assert audio[2] == -1.0


@pytest.mark.skipif(HAS_FASTER_WHISPER, reason="faster-whisper extra is installed")
def test_without_the_extra_the_error_names_it() -> None:
    with pytest.raises(ProviderError) as excinfo:
        build_provider("asr", "ears", ProviderConfig.model_validate({"type": "faster_whisper"}))
    assert "uv sync --extra faster-whisper" in str(excinfo.value)
