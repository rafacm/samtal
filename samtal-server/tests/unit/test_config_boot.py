"""The domain-half re-read a running server does on request.

`reload_domain_config` is the boot's own steps 2 to 5 with step 1 left
out, so what these check is exactly that: the stored half is fresh, the
file half is the running process's own, and every refusal the boot can
meet is met here too, because it is the same code meeting it.
"""

from pathlib import Path

import pytest

from samtal_server.config import Config
from samtal_server.config.boot import reload_domain_config
from samtal_server.config.loader import ConfigError
from samtal_server.config.secrets import (
    MASTER_KEY_ENV,
    SecretLocation,
    generate_key,
    load_keys,
)
from samtal_server.config.store import ConfigStore
from samtal_server.db import open_database

SECRET = "sk-test-4f8b2c9e-never-a-real-credential"


def running_config(directory: Path, **server: object) -> Config:
    """A server section of this test's own, pointed at a scratch
    database. An empty domain half is a valid configuration, which is
    what lets a test write the half it is about afterwards."""
    return Config(server={"database": {"dir": str(directory)}} | server)


def seeded(directory: Path, write) -> None:
    engine = open_database(directory)
    try:
        write(ConfigStore(engine, load_keys()))
    finally:
        engine.dispose()


def pipeline(store: ConfigStore) -> None:
    for stage in ("llm", "asr", "tts", "vad"):
        store.set_provider(stage, "mock", {"type": "mock"})
    store.set_agent_defaults(dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"))


def test_the_re_read_answers_with_what_the_database_holds_now(tmp_path: Path) -> None:
    directory = tmp_path / "db"
    running = running_config(directory)
    seeded(directory, pipeline)

    seeded(
        directory,
        lambda store: (
            store.set_mcp_server("weather", {"transport": "stdio", "command": "uvx"}),
            store.set_agent("sam", {"prompt": "You are Sam.", "mcp": ["weather"]}),
            store.set_default_agent("sam"),
        ),
    )
    reloaded = reload_domain_config(running)

    assert set(reloaded.config.mcp_servers) == {"weather"}
    assert [grant.server for grant in reloaded.config.mcp_for_agent("sam")] == [
        "weather"
    ]
    # The configuration the caller is holding is untouched: a re-read
    # answers with a new one rather than mutating the running snapshot.
    assert running.mcp_servers == {}


def test_the_re_read_keeps_the_running_server_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file half is not read again, deliberately: it is this
    process's own, down to the port it is listening on. An environment
    that says otherwise is the sharpest way to show it, since that is
    what the file half is read through at boot."""
    directory = tmp_path / "db"
    running = running_config(directory, port=8123, local_only=True)
    seeded(directory, pipeline)
    monkeypatch.setenv("SAMTAL_SERVER__PORT", "9999")
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(tmp_path / "elsewhere"))

    reloaded = reload_domain_config(running)

    assert reloaded.config.server == running.server
    assert reloaded.config.server.port == 8123
    assert reloaded.config.server.local_only is True


def test_the_re_read_validates_the_whole_snapshot(tmp_path: Path) -> None:
    """The rules about a runnable deployment, which no write enforces
    and every composition does, are enforced here too because it is the
    same composition: an agent nothing can reach is the one the store
    lets a caller arrive at."""
    directory = tmp_path / "db"
    running = running_config(directory)
    seeded(directory, lambda store: store.set_agent("sam", {"prompt": "You are Sam."}))

    with pytest.raises(ConfigError) as caught:
        reload_domain_config(running)

    assert "default_agent is required" in str(caught.value)
    assert "sam" in str(caught.value)


def test_the_re_read_verifies_the_stored_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verified before anything is built, so a key that was rotated away
    names the entity and the slot rather than surfacing as a decryption
    failure from the middle of building an MCP server."""
    directory = tmp_path / "db"
    running = running_config(directory)
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    location = SecretLocation.mcp_server("weather", "headers.Authorization")
    seeded(
        directory,
        lambda store: (
            pipeline(store),
            store.set_mcp_server(
                "weather", {"transport": "streamable_http", "url": "https://api.example/mcp"}
            ),
            store.set_secret(location, SECRET),
        ),
    )

    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    with pytest.raises(ConfigError) as caught:
        reload_domain_config(running)

    message = str(caught.value)
    assert location.describe() in message
    assert SECRET not in message


def test_the_re_read_carries_the_store_the_snapshot_was_loaded_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The secrets travel with the configuration for the reason they do
    at boot: they are needed exactly where a configuration becomes a
    running thing."""
    directory = tmp_path / "db"
    running = running_config(directory)
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    location = SecretLocation.mcp_server("weather", "headers.Authorization")
    seeded(
        directory,
        lambda store: (
            pipeline(store),
            store.set_mcp_server(
                "weather", {"transport": "streamable_http", "url": "https://api.example/mcp"}
            ),
            store.set_secret(location, SECRET),
        ),
    )

    reloaded = reload_domain_config(running)

    assert reloaded.secrets.secret(location) == SECRET
    # And the entity itself carries no value, which is what keeps the
    # models refusing an inline secret.
    assert "headers" not in reloaded.config.mcp_servers["weather"].model_fields_set
