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


class FakeWhisperModel:
    """Captures what reaches the engine without loading any weights."""

    def __init__(self, model: str, **kwargs: object) -> None:
        self.model = model
        self.ctor = kwargs
        self.calls: list[dict[str, object]] = []

    def transcribe(self, audio: object, **kwargs: object) -> tuple[object, object]:
        self.calls.append(kwargs)
        return iter([]), None


def built_with(
    monkeypatch: pytest.MonkeyPatch, **options: object
) -> tuple[object, "FakeWhisperModel"]:
    from samtal_server.providers import faster_whisper

    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeWhisperModel)
    provider = faster_whisper.build(
        "providers.asr.ears", ProviderConfig.model_validate({"type": "faster_whisper", **options})
    )
    return provider, provider._model  # type: ignore[attr-defined]


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster-whisper extra not installed")
async def test_defaults_decode_greedily_and_keep_engine_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, model = built_with(monkeypatch)
    await provider.transcribe(b"\x00\x00" * 160, 16000)
    (call,) = model.calls
    assert call == {
        "language": None,
        "beam_size": 1,
        "vad_filter": False,
        "condition_on_previous_text": True,
    }
    assert model.ctor["cpu_threads"] == 0


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster-whisper extra not installed")
async def test_configured_decode_options_reach_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, model = built_with(
        monkeypatch,
        language="en",
        beam_size=5,
        cpu_threads=3,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
        temperature=[0.0, 0.2],
    )
    await provider.transcribe(b"\x00\x00" * 160, 16000)
    (call,) = model.calls
    assert call == {
        "language": "en",
        "beam_size": 5,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 500},
        "condition_on_previous_text": False,
        "temperature": [0.0, 0.2],
    }
    assert model.ctor["cpu_threads"] == 3


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster-whisper extra not installed")
def test_a_wrongly_typed_decode_option_names_the_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from samtal_server.providers import faster_whisper

    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeWhisperModel)
    with pytest.raises(ProviderError) as excinfo:
        faster_whisper.build(
            "providers.asr.ears",
            ProviderConfig.model_validate({"type": "faster_whisper", "vad_filter": "yes"}),
        )
    assert "providers.asr.ears" in str(excinfo.value)
    assert '"vad_filter"' in str(excinfo.value)


@pytest.mark.skipif(HAS_FASTER_WHISPER, reason="faster-whisper extra is installed")
def test_without_the_extra_the_error_names_it() -> None:
    with pytest.raises(ProviderError) as excinfo:
        build_provider("asr", "ears", ProviderConfig.model_validate({"type": "faster_whisper"}))
    assert "uv sync --extra faster-whisper" in str(excinfo.value)
