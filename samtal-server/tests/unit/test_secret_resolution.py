"""Stored secrets at the point of use, against real providers and a
real MCP server.

Round-tripping an envelope proves the cryptography; it does not prove
that a credential written with `config set-secret` reaches the client
that has to send it. So these build the providers the registry builds
and spawn the stdio server the MCP tests spawn, and then look at where
the plaintext ended up: in the client and in the child process's
environment, and in no model, log record or error message.
"""

import logging
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, MultiFernet

from samtal_server.config import ConfigError
from samtal_server.config.models import McpServerConfig, ProviderConfig
from samtal_server.config.secrets import (
    SecretLocation,
    SecretStore,
    encrypt,
    generate_key,
)
from samtal_server.providers import ProviderError, build_provider
from samtal_server.providers.anthropic_llm import AnthropicLlm
from samtal_server.providers.openai_llm import OpenAiCompatibleLlm
from samtal_server.tools.mcp import McpServerManager

STDIO_SERVER = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"

CLAUDE = SecretLocation.provider("llm", "claude", "api_key")


def _chain(exc: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current)]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


def _store(*locations: SecretLocation, keys: MultiFernet | None = None) -> SecretStore:
    written = keys or MultiFernet([Fernet(generate_key())])
    return SecretStore(
        {where: encrypt(where, SECRET, written) for where in locations}, keys or written
    )


def test_a_stored_credential_reaches_a_real_anthropic_client() -> None:
    config = ProviderConfig.model_validate({"type": "anthropic", "model": "claude-sonnet-5"})

    provider = build_provider("llm", "claude", config, secrets=_store(CLAUDE))

    assert isinstance(provider, AnthropicLlm)
    assert provider._client.api_key == SECRET
    # The entry it was built from never held it, which is the whole
    # reason ciphertext lives beside the models rather than in them.
    assert SECRET not in repr(config)
    assert SECRET not in str(config.model_dump())


def test_a_stored_credential_reaches_a_real_openai_compatible_client() -> None:
    config = ProviderConfig.model_validate(
        {"type": "openai_compatible", "model": "qwen3", "base_url": "https://example.invalid/v1"}
    )
    stored = SecretLocation.provider("llm", "local", "api_key")

    provider = build_provider("llm", "local", config, secrets=_store(stored))

    assert isinstance(provider, OpenAiCompatibleLlm)
    assert provider._client.api_key == SECRET


def test_ciphertext_wins_over_the_reference_written_for_the_same_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """set-secret is the later and more deliberate act. The reference is
    not even read, so an unset variable behind it does not fail the boot
    the stored secret was set to fix."""
    monkeypatch.delenv("SAMTAL_TEST_KEY", raising=False)
    config = ProviderConfig.model_validate(
        {"type": "anthropic", "model": "claude-sonnet-5", "api_key_env": "SAMTAL_TEST_KEY"}
    )

    provider = build_provider("llm", "claude", config, secrets=_store(CLAUDE))

    assert provider._client.api_key == SECRET


def test_without_a_store_the_behaviour_is_exactly_todays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAMTAL_TEST_KEY", "sk-from-the-environment")
    config = ProviderConfig.model_validate(
        {"type": "anthropic", "model": "claude-sonnet-5", "api_key_env": "SAMTAL_TEST_KEY"}
    )

    assert build_provider("llm", "claude", config)._client.api_key == "sk-from-the-environment"

    # An empty store is not a store that answers: the environment
    # reference still resolves, and an unset one still fails the build.
    assert (
        build_provider("llm", "claude", config, secrets=SecretStore())._client.api_key
        == "sk-from-the-environment"
    )
    monkeypatch.delenv("SAMTAL_TEST_KEY")
    with pytest.raises(ProviderError, match="SAMTAL_TEST_KEY"):
        build_provider("llm", "claude", config, secrets=SecretStore())


def test_a_secret_for_another_entry_is_not_reachable_from_this_one() -> None:
    """The credential is keyed by the entry being built, so a sibling's
    secret is not a fallback."""
    config = ProviderConfig.model_validate({"type": "anthropic", "model": "claude-sonnet-5"})

    provider = build_provider("llm", "haiku", config, secrets=_store(CLAUDE))

    assert provider._client.api_key is None


def test_building_leaks_nothing_into_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    config = ProviderConfig.model_validate({"type": "anthropic", "model": "claude-sonnet-5"})

    with caplog.at_level(logging.DEBUG):
        build_provider("llm", "claude", config, secrets=_store(CLAUDE))

    assert SECRET not in caplog.text
    assert all(SECRET not in str(record.__dict__) for record in caplog.records)


def test_a_credential_that_cannot_be_opened_names_the_slot_and_not_the_value() -> None:
    written = MultiFernet([Fernet(generate_key())])
    wrong = MultiFernet([Fernet(generate_key())])
    store = SecretStore({CLAUDE: encrypt(CLAUDE, SECRET, written)}, wrong)
    config = ProviderConfig.model_validate({"type": "anthropic", "model": "claude-sonnet-5"})

    with pytest.raises(ConfigError) as caught:
        build_provider("llm", "claude", config, secrets=store)

    assert CLAUDE.describe() in str(caught.value)
    assert SECRET not in _chain(caught.value)


async def test_a_stored_secret_reaches_a_real_mcp_child_process() -> None:
    """The manager spawns the stdio server the MCP tests spawn, so the
    environment the secret lands in is a real process environment."""
    slot = SecretLocation.mcp_server("tools", "env.API_TOKEN")
    config = McpServerConfig.model_validate(
        {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(STDIO_SERVER)],
            "env": {"REGION": "eu"},
        }
    )

    manager = McpServerManager("tools", config, _store(slot))
    await manager.start()
    try:
        assert manager.up
        assert manager._env == {"REGION": "eu", "API_TOKEN": SECRET}
        assert SECRET not in str(config.model_dump())
    finally:
        await manager.stop()


def test_a_stored_header_shadows_the_reference_written_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEATHER_TOKEN", raising=False)
    slot = SecretLocation.mcp_server("weather", "headers.Authorization")
    config = McpServerConfig.model_validate(
        {
            "transport": "streamable_http",
            "url": "https://example.invalid/mcp",
            "headers": {"Authorization": "$WEATHER_TOKEN", "X-Region": "eu"},
        }
    )

    manager = McpServerManager("weather", config, _store(slot))

    assert manager._headers == {"X-Region": "eu", "Authorization": SECRET}


def test_an_mcp_server_without_a_store_still_reads_its_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEATHER_TOKEN", "from-the-environment")
    config = McpServerConfig.model_validate(
        {
            "transport": "streamable_http",
            "url": "https://example.invalid/mcp",
            "headers": {"Authorization": "$WEATHER_TOKEN"},
        }
    )

    assert McpServerManager("weather", config)._headers == {
        "Authorization": "from-the-environment"
    }
