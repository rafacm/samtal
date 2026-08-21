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
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, MultiFernet

from vinga_server.config import ConfigError
from vinga_server.config.models import McpServerConfig, ProviderConfig
from vinga_server.config.secrets import (
    SecretLocation,
    SecretStore,
    encrypt,
    generate_key,
)
from vinga_server.providers import ProviderError, build_entry
from vinga_server.providers.anthropic_llm import AnthropicLlm
from vinga_server.providers.openai_llm import OpenAiCompatibleLlm
from vinga_server.tools.mcp import McpServerManager

ENV_ECHO_SERVER = Path(__file__).parents[1] / "support" / "mcp_env_echo_server.py"

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


def key_of(provider: object) -> str | None:
    """What the built client will authenticate with.

    White-box, deliberately, and stated once here rather than at six
    assertions. The credential a deployment resolves is handed to the
    vendor SDK's client inside the provider and reported by no public
    surface, which is the property that makes the resolution safe: the
    value goes nowhere a reader could reach it. So the only way to
    establish that it arrived is to look where it went, and the negative
    half below, that a sibling entry's secret is not a fallback, has no
    observable form at all.
    """
    return provider._client.api_key  # type: ignore[attr-defined]


async def test_a_stored_credential_reaches_a_real_anthropic_client() -> None:
    config = ProviderConfig.model_validate({"type": "anthropic", "model": "claude-sonnet-5"})

    provider = await build_entry("llm", "claude", config, secrets=_store(CLAUDE))

    assert isinstance(provider, AnthropicLlm)
    assert key_of(provider) == SECRET
    # The entry it was built from never held it, which is the whole
    # reason ciphertext lives beside the models rather than in them.
    assert SECRET not in repr(config)
    assert SECRET not in str(config.model_dump())


async def test_a_stored_credential_reaches_a_real_openai_compatible_client() -> None:
    config = ProviderConfig.model_validate(
        {"type": "openai_compatible", "model": "qwen3", "base_url": "https://example.invalid/v1"}
    )
    stored = SecretLocation.provider("llm", "local", "api_key")

    provider = await build_entry("llm", "local", config, secrets=_store(stored))

    assert isinstance(provider, OpenAiCompatibleLlm)
    assert key_of(provider) == SECRET


async def test_ciphertext_wins_over_the_reference_written_for_the_same_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """set-secret is the later and more deliberate act. The reference is
    not even read, so an unset variable behind it does not fail the boot
    the stored secret was set to fix."""
    monkeypatch.delenv("VINGA_TEST_KEY", raising=False)
    config = ProviderConfig.model_validate(
        {"type": "anthropic", "model": "claude-sonnet-5", "api_key_env": "VINGA_TEST_KEY"}
    )

    provider = await build_entry("llm", "claude", config, secrets=_store(CLAUDE))

    assert key_of(provider) == SECRET


async def test_without_a_store_the_behaviour_is_exactly_todays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VINGA_TEST_KEY", "sk-from-the-environment")
    config = ProviderConfig.model_validate(
        {"type": "anthropic", "model": "claude-sonnet-5", "api_key_env": "VINGA_TEST_KEY"}
    )

    assert key_of(await build_entry("llm", "claude", config)) == "sk-from-the-environment"

    # An empty store is not a store that answers: the environment
    # reference still resolves, and an unset one still fails the build.
    assert (
        key_of(await build_entry("llm", "claude", config, secrets=SecretStore()))
        == "sk-from-the-environment"
    )
    monkeypatch.delenv("VINGA_TEST_KEY")
    with pytest.raises(ProviderError, match="references an unset environment variable"):
        await build_entry("llm", "claude", config, secrets=SecretStore())


async def test_a_secret_for_another_entry_is_not_reachable_from_this_one() -> None:
    """The credential is keyed by the entry being built, so a sibling's
    secret is not a fallback."""
    config = ProviderConfig.model_validate({"type": "anthropic", "model": "claude-sonnet-5"})

    provider = await build_entry("llm", "haiku", config, secrets=_store(CLAUDE))

    assert key_of(provider) is None


async def test_building_leaks_nothing_into_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    config = ProviderConfig.model_validate({"type": "anthropic", "model": "claude-sonnet-5"})

    with caplog.at_level(logging.DEBUG):
        await build_entry("llm", "claude", config, secrets=_store(CLAUDE))

    assert SECRET not in caplog.text
    assert all(SECRET not in str(record.__dict__) for record in caplog.records)


async def test_an_unset_reference_is_refused_without_repeating_what_was_written(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`api_key_env` takes the name of a variable, and the mistake that
    field invites is writing the credential into it instead. The model
    refuses anything that does not look like a variable name, which
    catches most of that; this is the line behind it, and it repeats
    nothing an operator wrote. main prints this sentence to stderr
    verbatim (main.py, the ConfigError and ProviderError arm) and the
    logs keep it, so the print below is what a terminal would show."""
    # Shaped like a variable name, so it gets past the model's own
    # check, and shaped so a substring test for it cannot match by
    # accident.
    written = "SK_TEST_4F8B2C9E_NEVER_A_REAL_CREDENTIAL"
    monkeypatch.delenv(written, raising=False)
    config = ProviderConfig.model_validate(
        {"type": "anthropic", "model": "claude-sonnet-5", "api_key_env": written}
    )

    with pytest.raises(ProviderError) as caught:
        await build_entry("llm", "claude", config)

    assert "providers.llm.claude" in str(caught.value)
    assert written not in _chain(caught.value)

    print(caught.value, file=sys.stderr)
    assert written not in capsys.readouterr().err


async def test_a_credential_that_cannot_be_opened_names_the_slot_and_not_the_value() -> None:
    written = MultiFernet([Fernet(generate_key())])
    wrong = MultiFernet([Fernet(generate_key())])
    store = SecretStore({CLAUDE: encrypt(CLAUDE, SECRET, written)}, wrong)
    config = ProviderConfig.model_validate({"type": "anthropic", "model": "claude-sonnet-5"})

    with pytest.raises(ConfigError) as caught:
        await build_entry("llm", "claude", config, secrets=store)

    assert CLAUDE.describe() in str(caught.value)
    assert SECRET not in _chain(caught.value)


async def test_a_stored_secret_reaches_a_real_mcp_child_process() -> None:
    """The delivery test, and it has to be the child process that says
    so: inspecting the manager would pass just as well if _connect
    stopped forwarding what it resolved.

    The spawned server answers whether its own environment holds the
    expected value, so the assertion travels the whole path (decrypt,
    spawn, environment) and prints a boolean rather than a credential
    when it fails."""
    slot = SecretLocation.mcp_server("tools", "env.API_TOKEN")
    config = McpServerConfig.model_validate(
        {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(ENV_ECHO_SERVER)],
            "env": {"REGION": "eu"},
        }
    )

    manager = McpServerManager("tools", config, _store(slot))
    await manager.start()
    try:
        assert manager.up
        assert await manager.call(
            "tools__env_matches", {"name": "API_TOKEN", "expected": SECRET}
        ) == ("true", False)
        # The literal beside it arrived too, so the stored slot did not
        # replace the mapping it joined.
        assert await manager.call("tools__env_matches", {"name": "REGION", "expected": "eu"}) == (
            "true",
            False,
        )
        assert SECRET not in str(config.model_dump())
    finally:
        await manager.stop()


async def test_the_manager_keeps_no_decrypted_credential() -> None:
    """A manager lives as long as the process. Resolution happens per
    connection, so the plaintext exists for the length of one connect
    and is not held in a long-lived object waiting to be picked up by a
    heap dump or a repr in a log line."""
    slot = SecretLocation.mcp_server("tools", "env.API_TOKEN")
    config = McpServerConfig.model_validate(
        {"transport": "stdio", "command": sys.executable, "args": [str(ENV_ECHO_SERVER)]}
    )

    manager = McpServerManager("tools", config, _store(slot))
    await manager.start()
    try:
        assert manager.up
        assert SECRET not in repr(manager.__dict__)
        assert all(SECRET not in repr(value) for value in vars(manager).values())
    finally:
        await manager.stop()


async def headers_on_the_wire(
    headers: dict[str, str], store: SecretStore | None
) -> dict[str, str]:
    """What an MCP server would actually receive from this entry.

    The stub refuses the handshake, which is fine: the request carrying
    the headers is what is under test, and a server that will not talk
    is a warning rather than a failure by design.
    """
    received: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the stdlib's spelling
            received.append({key.lower(): value for key, value in self.headers.items()})
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            """Silence: the stub is not the subject of the test."""

    stub = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=stub.serve_forever, daemon=True)
    thread.start()
    try:
        config = McpServerConfig.model_validate(
            {
                "transport": "streamable_http",
                "url": f"http://127.0.0.1:{stub.server_port}/mcp",
                "headers": headers,
            }
        )

        manager = McpServerManager("weather", config, store)
        await manager.start()
        await manager.stop()
    finally:
        stub.shutdown()
        thread.join(timeout=10)
        stub.server_close()

    assert received, "the client never reached the stub"
    return received[0]


async def test_a_stored_header_reaches_a_real_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same question for the other transport, answered by what
    arrived on the wire."""
    monkeypatch.delenv("WEATHER_TOKEN", raising=False)
    slot = SecretLocation.mcp_server("weather", "headers.Authorization")

    sent = await headers_on_the_wire(
        {"Authorization": "$WEATHER_TOKEN", "X-Region": "eu"}, _store(slot)
    )

    # The stored secret shadowed the $VAR that was never set, and the
    # header beside it came along.
    assert sent["authorization"] == SECRET
    assert sent["x-region"] == "eu"


async def test_an_mcp_server_without_a_store_still_reads_its_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No store is the deployment every configuration has today: the
    $VAR is read from the server's own environment, and an unset one
    fails the boot at construction."""
    monkeypatch.setenv("WEATHER_TOKEN", "from-the-environment")

    sent = await headers_on_the_wire({"Authorization": "$WEATHER_TOKEN"}, None)

    assert sent["authorization"] == "from-the-environment"

    monkeypatch.delenv("WEATHER_TOKEN")
    config = McpServerConfig.model_validate(
        {
            "transport": "streamable_http",
            "url": "https://example.invalid/mcp",
            "headers": {"Authorization": "$WEATHER_TOKEN"},
        }
    )
    with pytest.raises(ValueError, match="WEATHER_TOKEN"):
        McpServerManager("weather", config)
