"""The provider registry and the deterministic mock providers."""

import struct

import pytest

from samtal_server.audio import rms
from samtal_server.config import Config
from samtal_server.config.models import ProviderConfig
from samtal_server.providers import (
    ProviderError,
    TextDelta,
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


def test_a_provider_that_fails_to_construct_names_the_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local engine fetching its weights fails on a blocked host, a
    full volume or a name the hub does not have, and each arrived as a
    traceback from inside somebody else's library. With one entry per
    stage there was only one candidate; a configuration with several
    entries of a type left the operator to work out which."""
    from samtal_server.providers import registry

    def explode(label: str, config: ProviderConfig) -> object:
        raise OSError("no space left on device")

    monkeypatch.setattr(
        registry, "_factories", lambda: {"asr": {"faster_whisper": explode}}
    )
    with pytest.raises(ProviderError) as excinfo:
        build_provider("asr", "swedish", provider_config(type="faster_whisper"))
    assert "providers.asr.swedish" in str(excinfo.value)
    assert "faster_whisper" in str(excinfo.value)
    # The reason survives in the message, and the original in the chain,
    # so the traceback is not lost.
    assert "no space left on device" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, OSError)


def test_a_provider_error_raised_by_a_factory_is_left_exactly_as_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every existing message is composed by a factory, and tests assert
    on their wording (the missing-extra one names the extra to install,
    the local_only one names the type). Wrapping them would rewrite all
    of it."""
    from samtal_server.providers import registry

    def refuse(label: str, config: ProviderConfig) -> object:
        raise ProviderError(f'{label}: type "piper" needs the piper extra')

    monkeypatch.setattr(registry, "_factories", lambda: {"tts": {"piper": refuse}})
    with pytest.raises(ProviderError) as excinfo:
        build_provider("tts", "voice", provider_config(type="piper"))
    assert str(excinfo.value) == 'providers.tts.voice: type "piper" needs the piper extra'
    assert excinfo.value.__cause__ is None


def test_the_failing_entry_is_named_among_several_of_one_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The configuration this is about: language-locked personas, three
    ASR entries differing only in a pinned language. A traceback from
    inside a library leaves the operator to work out which of them it
    was."""
    from samtal_server.providers import registry

    def only_swedish_fails(label: str, config: ProviderConfig) -> object:
        if config.options.get("language") == "sv":
            raise RuntimeError("could not fetch the model")
        return MockAsr(text="hello")

    monkeypatch.setattr(
        registry, "_factories", lambda: {"asr": {"faster_whisper": only_swedish_fails}}
    )
    for language in ("en", "es"):
        build_provider("asr", language, provider_config(type="faster_whisper", language=language))
    with pytest.raises(ProviderError) as excinfo:
        build_provider("asr", "sv", provider_config(type="faster_whisper", language="sv"))
    assert str(excinfo.value).startswith("providers.asr.sv:")


def test_the_boolean_reader_takes_only_true_or_false() -> None:
    from samtal_server.providers.registry import OptionsReader

    reader = OptionsReader("providers.asr.ears", provider_config(type="mock", flag=True))
    assert reader.boolean("flag", False) is True
    assert reader.boolean("absent", False) is False
    with pytest.raises(ProviderError) as excinfo:
        OptionsReader(
            "providers.asr.ears", provider_config(type="mock", flag="yes")
        ).boolean("flag", False)
    assert '"flag"' in str(excinfo.value)


def test_the_numbers_reader_takes_a_list_or_a_single_number() -> None:
    from samtal_server.providers.registry import OptionsReader

    label = "providers.asr.ears"
    assert OptionsReader(label, provider_config(type="mock", t=[0, 0.2])).numbers(
        "t"
    ) == [0.0, 0.2]
    assert OptionsReader(label, provider_config(type="mock", t=0.4)).numbers("t") == [0.4]
    assert OptionsReader(label, provider_config(type="mock")).numbers("t") is None
    for bad in (["a"], [], True, [0.0, True]):
        with pytest.raises(ProviderError) as excinfo:
            OptionsReader(label, provider_config(type="mock", t=bad)).numbers("t")
        assert '"t"' in str(excinfo.value)


# Every provider type an entry configures a model on, with the options
# it needs to build and nothing else. faster-whisper is absent because
# building one downloads weights; its own suite asserts the same thing
# against a fake engine.
MODEL_BEARING = [
    ("llm", "anthropic", {"model": "claude-sonnet-5"}),
    (
        "llm",
        "openai_compatible",
        {"base_url": "http://localhost:11434/v1", "model": "qwen3:8b"},
    ),
    ("asr", "openai", {"model": "gpt-4o-mini-transcribe"}),
    ("tts", "openai", {"voice": "alloy", "model": "gpt-4o-mini-tts"}),
    ("tts", "elevenlabs", {"voice_id": "21m00Tcm4TlvDq8ikWAM", "model": "eleven_flash_v2_5"}),
]


@pytest.mark.parametrize(("stage", "type_", "options"), MODEL_BEARING)
def test_a_provider_that_runs_a_model_names_it_on_its_identity(
    monkeypatch: pytest.MonkeyPatch, stage: str, type_: str, options: dict[str, object]
) -> None:
    """The identity is what the events describe an entry with, so a
    model that only the provider's own `__init__` knows is a round
    nobody can attribute to a model (#120). Every real type reports the
    identifier it was configured with, unchanged."""
    monkeypatch.setenv("SAMTAL_TEST_KEY", "sk-test")
    provider = build_provider(
        stage,
        "entry",
        provider_config(type=type_, api_key_env="SAMTAL_TEST_KEY", **options),
    )
    assert provider.identity is not None
    assert provider.identity.model == options["model"]
    # And the rest of the identity is what it always was.
    assert (provider.identity.stage, provider.identity.name) == (stage, "entry")


def test_a_provider_with_no_model_to_name_carries_none() -> None:
    """A bundled engine and the mocks run nothing an operator chose, so
    the field is absent rather than invented, and the events that
    describe them carry one field fewer."""
    for stage in ("llm", "asr", "tts", "vad"):
        provider = build_provider(stage, "m", provider_config(type="mock"))
        assert provider.identity is not None
        assert provider.identity.model is None


def test_every_stage_builds_its_mock() -> None:
    assert isinstance(build_provider("llm", "m", provider_config(type="mock")), MockLlm)
    assert isinstance(build_provider("asr", "m", provider_config(type="mock")), MockAsr)
    assert isinstance(build_provider("tts", "m", provider_config(type="mock")), MockTts)
    assert isinstance(build_provider("vad", "m", provider_config(type="mock")), MockVad)


async def test_mock_asr_answers_the_configured_text_for_audio_and_nothing_for_none() -> None:
    asr = build_provider("asr", "m", provider_config(type="mock", text="tea please"))
    assert (await asr.transcribe(b"\x00\x01" * 320, 16000)).text == "tea please"
    assert (await asr.transcribe(b"", 16000)).text == ""


async def test_mock_llm_formats_the_last_user_turn_into_the_reply() -> None:
    llm = build_provider("llm", "m", provider_config(type="mock"))
    turns = [
        Turn("user", "one"),
        Turn("assistant", "You said one."),
        Turn("user", "two sugars"),
    ]
    # The stream opens with its liveness announcement; the words are
    # the text deltas that follow.
    reply = "".join(
        [
            event.text
            async for event in llm.stream("prompt", turns)
            if isinstance(event, TextDelta)
        ]
    )
    assert reply == "You said two sugars."


async def test_mock_llm_can_quote_the_prompt_it_was_given() -> None:
    llm = build_provider("llm", "m", provider_config(type="mock", reply="{system}: {text}."))
    reply = "".join(
        [
            event.text
            async for event in llm.stream("POET", [Turn("user", "hi")])
            if isinstance(event, TextDelta)
        ]
    )
    assert reply == "POET: hi."


async def test_mock_tts_speaks_the_configured_tone() -> None:
    for tone_hz in (440.0, 880.0):
        tts = build_provider("tts", "m", provider_config(type="mock", tone_hz=tone_hz))
        audio = b"".join([chunk async for chunk in tts.synthesize("A sentence to speak.")])
        assert abs(tone_of(audio, tts.sample_rate) - tone_hz) < 10


def tone_of(pcm: bytes, sample_rate: int) -> float:
    """The frequency of a pure tone, from its zero crossings."""
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    crossings = sum(
        1 for a, b in zip(samples, samples[1:], strict=False) if (a < 0) != (b < 0)
    )
    return crossings / 2 / (len(samples) / sample_rate)


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
    assert "agent_defaults.asr" in str(excinfo.value)


def test_agent_defaults_complete_a_pipeline_an_agent_only_half_names() -> None:
    config = Config(
        providers={
            "llm": {"mock": {"type": "mock"}},
            "asr": {"mock": {"type": "mock"}},
            "tts": {"alto": {"type": "mock"}, "tenor": {"type": "mock", "tone_hz": 880.0}},
            "vad": {"mock": {"type": "mock"}},
        },
        agent_defaults={"llm": "mock", "asr": "mock", "tts": "alto", "vad": "mock"},
        agents={"poet": {"prompt": "POET", "tts": "tenor"}, "tutor": {"prompt": "TUTOR"}},
        default_agent="tutor",
    )
    providers = build_agent_providers(config)
    # The inherited stages are literally one instance; the overridden one
    # is the agent's own.
    assert providers["poet"].llm is providers["tutor"].llm
    assert providers["poet"].vad is providers["tutor"].vad
    assert providers["poet"].tts is not providers["tutor"].tts
    # And the personas are not here at all: what this builds is the four
    # engines, and the prompt has one source, which is the
    # configuration.
    assert not hasattr(providers["poet"], "prompt")
    assert config.prompt_for_agent("poet") == "POET"
    assert config.prompt_for_agent("tutor") == "TUTOR"


def test_an_agent_default_naming_a_broken_provider_fails_the_boot() -> None:
    config = Config(
        providers={stage: {"mock": {"type": "mock"}} for stage in ("asr", "tts", "vad")}
        | {"llm": {"broken": {"type": "mock", "repply": "typo"}}},
        agent_defaults=dict.fromkeys(("asr", "tts", "vad"), "mock") | {"llm": "broken"},
        agents={"assistant": {}},
        default_agent="assistant",
    )
    with pytest.raises(ProviderError, match="providers.llm.broken"):
        build_agent_providers(config)


def test_mock_vad_hands_out_independent_endpointers() -> None:
    vad = build_provider("vad", "m", provider_config(type="mock", max_utterance_ms=100.0))
    first, second = vad.new_endpointer(), vad.new_endpointer()
    assert first is not second
    loud = b"\x00\x7f" * 1600  # 100 ms of loud not-quite-noise at 16 kHz
    assert first.feed(loud) is True  # the 100 ms cap fires
    assert second.feed(b"\x00\x00" * 1600) is False  # silence alone never ends
