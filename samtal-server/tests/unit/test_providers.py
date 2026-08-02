"""The provider registry and the deterministic mock providers."""

import pytest

from samtal_server.audio.endpointing import rms
from samtal_server.config import Config
from samtal_server.config.models import ProviderConfig
from samtal_server.providers import (
    ProviderError,
    Turn,
    build_agent_providers,
    build_provider,
)
from samtal_server.providers.mock import MockAsr, MockLlm, MockTts, MockVad


def provider_config(**data: object) -> ProviderConfig:
    return ProviderConfig.model_validate(data)


def test_an_unknown_provider_type_names_the_entry_and_the_known_types() -> None:
    with pytest.raises(ProviderError) as excinfo:
        build_provider("tts", "voice", provider_config(type="espeak"))
    assert "providers.tts.voice" in str(excinfo.value)
    assert "espeak" in str(excinfo.value)
    assert "mock" in str(excinfo.value)


def test_an_unknown_option_is_rejected_at_build_time() -> None:
    with pytest.raises(ProviderError) as excinfo:
        build_provider("asr", "ears", provider_config(type="mock", txet="typo"))
    assert "providers.asr.ears" in str(excinfo.value)
    assert "txet" in str(excinfo.value)


def test_a_wrongly_typed_option_is_rejected_at_build_time() -> None:
    with pytest.raises(ProviderError) as excinfo:
        build_provider("vad", "ears", provider_config(type="mock", threshold="loud"))
    assert '"threshold"' in str(excinfo.value)


def test_every_stage_builds_its_mock() -> None:
    assert isinstance(build_provider("llm", "m", provider_config(type="mock")), MockLlm)
    assert isinstance(build_provider("asr", "m", provider_config(type="mock")), MockAsr)
    assert isinstance(build_provider("tts", "m", provider_config(type="mock")), MockTts)
    assert isinstance(build_provider("vad", "m", provider_config(type="mock")), MockVad)


async def test_mock_asr_answers_the_configured_text_for_audio_and_nothing_for_none() -> None:
    asr = build_provider("asr", "m", provider_config(type="mock", text="tea please"))
    assert await asr.transcribe(b"\x00\x01" * 320, 16000) == "tea please"
    assert await asr.transcribe(b"", 16000) == ""


async def test_mock_llm_formats_the_last_user_turn_into_the_reply() -> None:
    llm = build_provider("llm", "m", provider_config(type="mock"))
    turns = [
        Turn("user", "one"),
        Turn("assistant", "You said one."),
        Turn("user", "two sugars"),
    ]
    reply = "".join([delta async for delta in llm.stream("prompt", turns)])
    assert reply == "You said two sugars."


async def test_mock_tts_speaks_longer_for_longer_text() -> None:
    tts = build_provider("tts", "m", provider_config(type="mock"))
    assert tts.sample_rate == 24_000

    async def spoken(text: str) -> bytes:
        return b"".join([chunk async for chunk in tts.synthesize(text)])

    short = await spoken("Hi.")
    long = await spoken("A much longer sentence than the short one was.")
    assert len(long) > len(short)
    assert rms(short) > 1000


def test_agents_share_one_instance_per_named_provider() -> None:
    config = Config(
        providers={stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")},
        agents={
            "assistant": dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
            "kitchen": dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        },
        default_agent="assistant",
    )
    providers = build_agent_providers(config)
    assert providers["assistant"].llm is providers["kitchen"].llm
    assert providers["assistant"].tts is providers["kitchen"].tts


def test_an_agent_without_a_full_pipeline_fails_the_boot() -> None:
    config = Config(
        providers={"llm": {"mock": {"type": "mock"}}},
        agents={"assistant": {"llm": "mock"}},
        default_agent="assistant",
    )
    with pytest.raises(ProviderError) as excinfo:
        build_agent_providers(config)
    assert "agents.assistant" in str(excinfo.value)
    assert "asr" in str(excinfo.value)


def test_mock_vad_hands_out_independent_endpointers() -> None:
    vad = build_provider("vad", "m", provider_config(type="mock", max_utterance_ms=100.0))
    first, second = vad.new_endpointer(), vad.new_endpointer()
    assert first is not second
    loud = b"\x00\x7f" * 1600  # 100 ms of loud not-quite-noise at 16 kHz
    assert first.feed(loud) is True  # the 100 ms cap fires
    assert second.feed(b"\x00\x00" * 1600) is False  # silence alone never ends
