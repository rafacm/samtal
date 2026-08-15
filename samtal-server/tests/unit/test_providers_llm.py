"""Building the LLM providers and shaping their requests. Actual
streaming needs a live endpoint; that is the local lane's job."""

import pytest

from samtal_server.config.models import ProviderConfig
from samtal_server.providers import ProviderError, Turn, build_provider
from samtal_server.providers.anthropic_llm import AnthropicLlm
from samtal_server.providers.kit import DEFAULT_TIMEOUT_S, MAX_RETRIES
from samtal_server.providers.openai_llm import OpenAiCompatibleLlm, chat_messages


def provider_config(**data: object) -> ProviderConfig:
    return ProviderConfig.model_validate(data)


def test_anthropic_requires_a_model() -> None:
    with pytest.raises(ProviderError, match='"model" is required'):
        build_provider("llm", "claude", provider_config(type="anthropic"))


def test_a_named_but_unset_api_key_env_fails_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SAMTAL_TEST_KEY", raising=False)
    config = provider_config(
        type="anthropic", model="claude-sonnet-5", api_key_env="SAMTAL_TEST_KEY"
    )
    with pytest.raises(ProviderError, match="references an unset environment variable") as failure:
        build_provider("llm", "claude", config)
    # The entry, not the reference: what an operator wrote in that field
    # is not repeated back into a sentence main prints and the logs keep.
    assert "providers.llm.claude" in str(failure.value)
    assert "SAMTAL_TEST_KEY" not in str(failure.value)


def test_a_set_api_key_env_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAMTAL_TEST_KEY", "sk-test")
    config = provider_config(
        type="anthropic", model="claude-sonnet-5", api_key_env="SAMTAL_TEST_KEY"
    )
    assert isinstance(build_provider("llm", "claude", config), AnthropicLlm)


def test_openai_compatible_requires_a_base_url() -> None:
    with pytest.raises(ProviderError, match='"base_url" is required'):
        build_provider("llm", "local", provider_config(type="openai_compatible", model="qwen3"))


def test_openai_compatible_builds_keyless_for_local_endpoints() -> None:
    config = provider_config(
        type="openai_compatible", base_url="http://localhost:11434/v1", model="qwen3:8b"
    )
    assert isinstance(build_provider("llm", "local", config), OpenAiCompatibleLlm)


# --- the client each entry builds ------------------------------------


def test_the_anthropic_client_carries_the_timeout_and_sends_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injecting a client proves nothing about the one a deployment gets:
    a constructor that forgot both arguments would pass every test that
    hands its own client in. Until this issue these clients had no
    timeout at all and the SDK's two retries, which would make any
    bound three attempts plus backoff inside one turn."""
    monkeypatch.setenv("SAMTAL_TEST_KEY", "sk-test")
    built = build_provider(
        "llm",
        "claude",
        provider_config(type="anthropic", model="claude-sonnet-5", api_key_env="SAMTAL_TEST_KEY"),
    )
    assert isinstance(built, AnthropicLlm)
    assert built._client.timeout == DEFAULT_TIMEOUT_S
    assert built._client.max_retries == MAX_RETRIES


def test_the_openai_compatible_client_carries_the_timeout_and_sends_one_attempt() -> None:
    built = build_provider(
        "llm",
        "local",
        provider_config(
            type="openai_compatible", base_url="http://localhost:11434/v1", model="qwen3:8b"
        ),
    )
    assert isinstance(built, OpenAiCompatibleLlm)
    assert built._client.timeout == DEFAULT_TIMEOUT_S
    assert built._client.max_retries == MAX_RETRIES


def test_an_injected_anthropic_client_is_used_as_given() -> None:
    """The seam the other three cloud providers already had, and what
    the tool-calling tests now arrive through."""
    given = object()
    llm = AnthropicLlm(
        model="claude-sonnet-5",
        max_tokens=64,
        api_key="sk-test",
        client=given,  # type: ignore[arg-type]
    )
    assert llm._client is given


def test_an_injected_openai_compatible_client_is_used_as_given() -> None:
    given = object()
    llm = OpenAiCompatibleLlm(
        base_url="http://localhost:11434/v1",
        model="qwen3:8b",
        max_tokens=64,
        api_key=None,
        client=given,  # type: ignore[arg-type]
    )
    assert llm._client is given


def test_chat_messages_prepend_the_system_prompt() -> None:
    turns = [Turn("user", "hi"), Turn("assistant", "hello"), Turn("user", "bye")]
    assert chat_messages("be brief", turns) == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye"},
    ]
    assert chat_messages("", turns)[0]["role"] == "user"
