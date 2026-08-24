"""The faster-whisper provider, as far as a unit test can go.

Transcription with a real model needs weights from the network, which
the test lanes never download; that path is exercised by the local lane
and the device checkpoint. What runs here depends on whether the extra
is installed: the PCM conversion and the plumbing from the validated
options to the engine (against a fake engine) when it is, the helpful
registry error when it is not.

What this file no longer holds is what the type ACCEPTS. That was a
ladder of reader calls inside the builder and is `FasterWhisperOptions`
now, so the cases about wrong types, unknown keys and the defaults live
in `test_provider_options.py`, where they run in every lane rather than
only where the extra is installed.
"""

import importlib.util
from types import SimpleNamespace

import pytest

from vinga_server.config.models import ProviderConfig
from vinga_server.config.provider_options import FasterWhisperOptions
from vinga_server.providers import ProviderError, build_entry

HAS_FASTER_WHISPER = importlib.util.find_spec("faster_whisper") is not None


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster-whisper extra not installed")
def test_pcm_converts_to_normalized_float32() -> None:
    from vinga_server.providers.faster_whisper import pcm_to_float

    audio = pcm_to_float(b"\x00\x00\xff\x7f\x00\x80")  # 0, 32767, -32768
    assert audio.dtype.name == "float32"
    assert audio[0] == 0.0
    assert 0.99 < audio[1] <= 1.0
    assert audio[2] == -1.0


class FakeWhisperModel:
    """Captures what reaches the engine without loading any weights.

    Mimics the engine's language behaviour: a pinned language comes back
    as-is with probability 1, no language means "detection" answered
    with the scripted `detections` (one per call, last one repeating)."""

    detections: list[tuple[str, float]] = [("en", 0.9)]

    def __init__(self, model: str, **kwargs: object) -> None:
        self.model = model
        self.ctor = kwargs
        self.calls: list[dict[str, object]] = []

    def transcribe(self, audio: object, **kwargs: object) -> tuple[object, object]:
        self.calls.append(kwargs)
        if kwargs.get("language") is not None:
            info = SimpleNamespace(language=kwargs["language"], language_probability=1.0)
        else:
            detected = self.detections[min(len(self.calls) - 1, len(self.detections) - 1)]
            info = SimpleNamespace(language=detected[0], language_probability=detected[1])
        return iter([]), info


def built_with(
    monkeypatch: pytest.MonkeyPatch,
    detections: list[tuple[str, float]] | None = None,
    **options: object,
) -> tuple[object, "FakeWhisperModel"]:
    from vinga_server.providers import faster_whisper

    class ScriptedModel(FakeWhisperModel):
        pass

    ScriptedModel.detections = detections or [("en", 0.9)]
    monkeypatch.setattr(faster_whisper, "WhisperModel", ScriptedModel)
    # Through the model rather than around it, so what reaches the engine
    # below is what a written fragment would produce: the options are
    # validated exactly as `construct_provider` validates them.
    provider = faster_whisper.build(
        "providers.asr.ears",
        ProviderConfig.model_validate({"type": "faster_whisper", **options}),
        FasterWhisperOptions.model_validate(options),
    )
    # White-box, for the same reason the cloud providers' clients are:
    # the model a deployment gets is built inside the provider and
    # handed to nobody, so which decode flags reached it is observable
    # only by loading a real model and listening to what comes back.
    return provider, provider._engine  # type: ignore[attr-defined]


AUDIO = b"\x00\x00" * 160


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster-whisper extra not installed")
def test_the_configured_model_is_named_on_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The engine is private and the model identifier is not: it is what
    the registry stamps onto the entry's identity, and from there what
    the provider-bearing events carry as `model` (#120)."""
    provider, engine = built_with(monkeypatch, model="medium")
    assert provider.model == "medium"
    assert engine.model == "medium"


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster-whisper extra not installed")
async def test_defaults_decode_greedily_and_keep_engine_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, model = built_with(monkeypatch)
    await provider.transcribe(AUDIO, 16000)
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
    await provider.transcribe(AUDIO, 16000)
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
async def test_detection_metadata_reaches_the_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, _ = built_with(monkeypatch, detections=[("es", 0.97)])
    result = await provider.transcribe(AUDIO, 16000)
    assert result.language == "es"
    assert result.language_confidence == 0.97
    # every_utterance is the default: nothing asks the session to cache.
    assert result.lock_language is None


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster-whisper extra not installed")
async def test_a_hint_pins_the_language_and_skips_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, model = built_with(monkeypatch)
    result = await provider.transcribe(AUDIO, 16000, language_hint="sv")
    (call,) = model.calls
    assert call["language"] == "sv"
    assert result.language == "sv"
    # A pinned call detected nothing, so there is no confidence to report.
    assert result.language_confidence is None


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster-whisper extra not installed")
async def test_a_configured_language_beats_the_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, model = built_with(monkeypatch, language="en")
    await provider.transcribe(AUDIO, 16000, language_hint="sv")
    (call,) = model.calls
    assert call["language"] == "en"


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster-whisper extra not installed")
async def test_a_low_confidence_detection_falls_back_before_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, model = built_with(
        monkeypatch, detections=[("pl", 0.39)], language_fallback="en"
    )
    result = await provider.transcribe(AUDIO, 16000)
    first, second = model.calls
    assert first["language"] is None
    assert second["language"] == "en"
    assert result.language == "en"
    # The confidence of the distrusted detection stays on the record,
    # which is what lets an operator see the fallback engaging.
    assert result.language_confidence == 0.39
    assert result.lock_language is None


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster-whisper extra not installed")
async def test_a_confident_detection_is_not_second_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, model = built_with(
        monkeypatch, detections=[("es", 0.97)], language_fallback="en"
    )
    result = await provider.transcribe(AUDIO, 16000)
    assert len(model.calls) == 1
    assert result.language == "es"


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster-whisper extra not installed")
async def test_detect_once_locks_only_a_confident_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    confident, _ = built_with(monkeypatch, detections=[("es", 0.97)], language_detect="once")
    result = await confident.transcribe(AUDIO, 16000)
    assert result.lock_language == "es"

    doubtful, _ = built_with(monkeypatch, detections=[("pl", 0.39)], language_detect="once")
    result = await doubtful.transcribe(AUDIO, 16000)
    assert result.lock_language is None

    pinned, _ = built_with(monkeypatch, language="en", language_detect="once")
    result = await pinned.transcribe(AUDIO, 16000)
    assert result.lock_language is None


@pytest.mark.skipif(HAS_FASTER_WHISPER, reason="faster-whisper extra is installed")
async def test_without_the_extra_the_error_names_it() -> None:
    with pytest.raises(ProviderError) as excinfo:
        await build_entry("asr", "ears", ProviderConfig.model_validate({"type": "faster_whisper"}))
    assert "uv sync --extra faster-whisper" in str(excinfo.value)
    # And the ImportError does not travel with it. It carries the module
    # search path and a traceback through somebody else's package, and
    # this sentence is printed to an operator as it is.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None
