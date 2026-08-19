"""Egress marking and the server.local_only boot check (#30).

Every provider type carries a class-level `egress` marking of its own;
building an egress-marked provider under `server.local_only: true` fails
the boot with an error naming the stage and provider, and building one
whose class declared no marking, or declared something that is not one
of the three, fails in any mode (#136). openai_compatible is the special
case: its base_url decides, so under local_only the entry needs the
operator's own `egress: false` declaration.
"""

import importlib.util
from collections.abc import Callable

import pytest

from vinga_server.config import Config
from vinga_server.config.models import ProviderConfig
from vinga_server.providers import (
    AsrProvider,
    LlmProvider,
    Provider,
    ProviderError,
    TtsProvider,
    VadProvider,
    build_agent_providers,
    build_provider,
    registry,
)
from vinga_server.providers.anthropic_llm import AnthropicLlm
from vinga_server.providers.mock import MockAsr, MockLlm, MockTts, MockVad
from vinga_server.providers.openai_llm import OpenAiCompatibleLlm
from vinga_server.providers.silero import SileroVad

HAS_FASTER_WHISPER = importlib.util.find_spec("faster_whisper") is not None
HAS_PIPER = importlib.util.find_spec("piper") is not None

LOCAL_BASE_URL = "http://localhost:11434/v1"


def provider_config(**data: object) -> ProviderConfig:
    return ProviderConfig.model_validate(data)


def config_with_llm(llm_entry: dict[str, object], local_only: bool) -> Config:
    """One agent on the given LLM entry, mocks for the other stages."""
    return Config(
        server={"local_only": local_only},
        providers={
            "llm": {"brain": llm_entry},
            "asr": {"ears": {"type": "mock"}},
            "tts": {"voice": {"type": "mock"}},
            "vad": {"gate": {"type": "mock"}},
        },
        agents={
            "assistant": {"llm": "brain", "asr": "ears", "tts": "voice", "vad": "gate"}
        },
        default_agent="assistant",
    )


def build_a_throwaway_llm(
    monkeypatch: pytest.MonkeyPatch, make: Callable[[], object]
) -> object:
    """Build a throwaway provider class through `build_provider`, with
    local_only off.

    The marking is checked where a provider is built rather than where
    its class is defined, so a test about a class that declares wrongly
    has to reach the build. The factory table is rebuilt on every call,
    which is why the type is registered by replacing the function that
    returns it."""
    monkeypatch.setattr(
        registry,
        "_factories",
        lambda: {"llm": {"throwaway": lambda label, config: make()}},
    )
    return build_provider("llm", "brain", provider_config(type="throwaway"))


def test_local_engines_and_mocks_are_marked_local() -> None:
    assert SileroVad.egress is False
    for mock_class in (MockLlm, MockAsr, MockTts, MockVad):
        assert mock_class.egress is False


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster-whisper extra not installed")
def test_faster_whisper_is_marked_local() -> None:
    from vinga_server.providers.faster_whisper import FasterWhisperAsr

    assert FasterWhisperAsr.egress is False


@pytest.mark.skipif(not HAS_PIPER, reason="piper extra not installed")
def test_piper_is_marked_local() -> None:
    from vinga_server.providers.piper_tts import PiperTts

    assert PiperTts.egress is False


def test_the_cloud_and_configurable_types_carry_their_marking() -> None:
    assert AnthropicLlm.egress is True
    assert OpenAiCompatibleLlm.egress is None


def test_a_type_that_forgot_to_declare_is_refused_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Forgetful(Provider):
        pass

    with pytest.raises(ProviderError) as excinfo:
        build_a_throwaway_llm(monkeypatch, Forgetful)
    message = str(excinfo.value)
    assert "providers.llm.brain" in message
    assert "Forgetful" in message


def test_the_provider_bases_declare_no_egress_at_runtime() -> None:
    # A default on a base is the same hole as a default in the check:
    # every subclass would inherit an answer nobody wrote.
    for base in (Provider, VadProvider, AsrProvider, LlmProvider, TtsProvider):
        assert not hasattr(base, "egress")


def test_an_unmarked_subclass_does_not_ride_its_parents_marking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Quiet(MockLlm):
        pass

    with pytest.raises(ProviderError) as excinfo:
        build_a_throwaway_llm(monkeypatch, lambda: Quiet("hello"))
    message = str(excinfo.value)
    assert "Quiet" in message


def test_a_marking_that_is_not_one_of_the_three_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Sloppy(Provider):
        egress = 0

    with pytest.raises(ProviderError) as excinfo:
        build_a_throwaway_llm(monkeypatch, Sloppy)
    message = str(excinfo.value)
    assert "Sloppy" in message
    # Value-free like every other refusal: what was read never reaches
    # the message, so a marking holding a credential-shaped typo cannot
    # print one.
    assert "0" not in message


def test_local_only_refuses_an_egress_provider_naming_stage_and_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    config = config_with_llm(
        {"type": "anthropic", "model": "claude-sonnet-5", "api_key_env": "ANTHROPIC_API_KEY"},
        local_only=True,
    )
    with pytest.raises(ProviderError) as excinfo:
        build_agent_providers(config)
    message = str(excinfo.value)
    assert "providers.llm.brain" in message
    assert '"anthropic"' in message
    assert "local_only" in message


def test_local_only_boots_a_pipeline_of_declared_local_providers() -> None:
    config = config_with_llm(
        {
            "type": "openai_compatible",
            "base_url": LOCAL_BASE_URL,
            "model": "qwen3:8b",
            "egress": False,
        },
        local_only=True,
    )
    providers = build_agent_providers(config)
    assert isinstance(providers["assistant"].llm, OpenAiCompatibleLlm)


def test_openai_compatible_without_a_declaration_is_refused_under_local_only() -> None:
    config = config_with_llm(
        {"type": "openai_compatible", "base_url": LOCAL_BASE_URL, "model": "qwen3:8b"},
        local_only=True,
    )
    with pytest.raises(ProviderError) as excinfo:
        build_agent_providers(config)
    message = str(excinfo.value)
    assert "providers.llm.brain" in message
    assert '"egress: false"' in message


def test_openai_compatible_declared_egress_is_refused_under_local_only() -> None:
    entry = provider_config(
        type="openai_compatible",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        egress=True,
    )
    with pytest.raises(ProviderError, match="sends session data off this host"):
        build_provider("llm", "brain", entry, local_only=True)


def test_an_egress_declaration_on_a_type_that_knows_its_own_is_rejected() -> None:
    with pytest.raises(ProviderError) as excinfo:
        build_provider("asr", "ears", provider_config(type="mock", egress=False))
    message = str(excinfo.value)
    assert "providers.asr.ears" in message
    assert 'decided by type "mock"' in message


def test_without_local_only_an_undeclared_openai_compatible_still_builds() -> None:
    provider = build_provider(
        "llm",
        "brain",
        provider_config(type="openai_compatible", base_url=LOCAL_BASE_URL, model="qwen3:8b"),
    )
    assert isinstance(provider, OpenAiCompatibleLlm)
