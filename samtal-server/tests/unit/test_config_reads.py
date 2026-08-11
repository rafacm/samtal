"""Reading one entity, through the repository.

Existence is the repository's decision, so a read of something that is
not there is refused there, once, in the words the CLI has always
printed and with the type the API answers 404 to. What comes back beside
the entity is its stored-secret slots, each marked with the key it
displaces, which is the one fact a masked read exists to convey and the
one thing the model-shaped half can never carry.
"""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet, MultiFernet

from samtal_server.config.loader import ConfigError, UnknownEntityError
from samtal_server.config.secrets import SecretLocation, generate_key
from samtal_server.config.store import ConfigStore
from samtal_server.db import open_database

# Not a real credential, and shaped so a substring check for one cannot
# match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"
OTHER_SECRET = "tok-test-7a1d3f60-never-a-real-credential"


@pytest.fixture
def store(tmp_path: Path):
    engine = open_database(tmp_path / "db")
    try:
        yield ConfigStore(engine, MultiFernet([Fernet(generate_key())]))
    finally:
        engine.dispose()


def _populate(store: ConfigStore) -> None:
    store.set_provider(
        "llm",
        "claude",
        {"type": "anthropic", "model": "m", "api_key_env": "ANTHROPIC_API_KEY"},
    )
    store.set_provider("tts", "voice", {"type": "piper", "model": "es"})
    store.set_mcp_server(
        "weather",
        {
            "transport": "streamable_http",
            "url": "https://example.invalid/mcp",
            "headers": {"Authorization": "$WEATHER_TOKEN", "X-Region": "eu"},
        },
    )
    store.set_agent("sam", {"prompt": "You are Sam.", "tts": "voice"})
    store.bind_device("AA-BB-CC-DD-EE-FF", ["sam"])
    store.set_default_agent("sam")


def test_every_kind_reads_back_what_was_written(store: ConfigStore) -> None:
    _populate(store)

    assert store.read_provider("llm", "claude").entry.type == "anthropic"
    assert store.read_mcp_server("weather").entry.transport == "streamable_http"
    assert store.read_agent("sam").entry.prompt == "You are Sam."
    assert store.read_agent_defaults().entry.llm is None
    # The canonical form of the MAC, whichever spelling asked for it.
    assert store.read_device("aa:bb:cc:dd:ee:ff").entry == ["sam"]
    assert store.read_default_agent() == "sam"


def test_reading_something_that_is_not_there_names_it(store: ConfigStore) -> None:
    """The 404 set, with the sentences the CLI has always printed: one
    vocabulary whichever way an operator reached the read."""
    cases = [
        (lambda: store.read_provider("llm", "ghost"), "providers.llm.ghost: no such provider"),
        (lambda: store.read_mcp_server("ghost"), "mcp_servers.ghost: no such MCP server"),
        (lambda: store.read_agent("ghost"), "agents.ghost: no such agent"),
        (
            lambda: store.read_device("aa:bb:cc:dd:ee:ff"),
            "devices.aa:bb:cc:dd:ee:ff: no such device",
        ),
    ]
    for call, message in cases:
        with pytest.raises(UnknownEntityError) as caught:
            call()
        assert str(caught.value) == message


def test_a_read_that_addresses_nothing_addressable_stays_plain(store: ConfigStore) -> None:
    """A stage that is not a stage and a MAC that is not a MAC are the
    caller's mistake rather than a missing entity, so they keep the type
    the API answers 422 to."""
    for call in (
        lambda: store.read_provider("nonsense", "claude"),
        lambda: store.read_device("not-a-mac"),
    ):
        with pytest.raises(ConfigError) as caught:
            call()
        assert type(caught.value) is ConfigError, caught.value


def test_the_singleton_and_the_default_agent_are_never_missing(store: ConfigStore) -> None:
    """An unwritten agent_defaults is the empty entry and an unset
    default agent is None: both are configurations, not absences."""
    assert store.read_agent_defaults().entry.model_dump(exclude_none=True) == {}
    assert store.read_default_agent() is None


def test_a_read_names_its_stored_slots_and_what_they_shadow(store: ConfigStore) -> None:
    _populate(store)
    store.set_secret(SecretLocation.provider("llm", "claude", "api_key"), SECRET)
    store.set_secret(SecretLocation.mcp_server("weather", "headers.Authorization"), OTHER_SECRET)

    provider = store.read_provider("llm", "claude")
    mcp = store.read_mcp_server("weather")

    assert [(item.location.slot, item.shadows) for item in provider.secrets] == [
        ("api_key", "api_key_env")
    ]
    assert [(item.location.slot, item.shadows) for item in mcp.secrets] == [
        ("headers.Authorization", "headers.Authorization")
    ]


def test_a_stored_slot_the_entity_writes_no_reference_for_shadows_nothing(
    store: ConfigStore,
) -> None:
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "m"})
    store.set_mcp_server("home", {"transport": "stdio", "command": "uvx"})
    store.set_secret(SecretLocation.provider("llm", "claude", "api_key"), SECRET)
    store.set_secret(SecretLocation.mcp_server("home", "env.HOME_TOKEN"), OTHER_SECRET)

    assert store.read_provider("llm", "claude").secrets[0].shadows is None
    assert store.read_mcp_server("home").secrets[0].shadows is None


def test_a_kind_that_holds_no_secret_reads_with_none(store: ConfigStore) -> None:
    _populate(store)

    assert store.read_agent("sam").secrets == ()
    assert store.read_agent_defaults().secrets == ()
    assert store.read_device("aa:bb:cc:dd:ee:ff").secrets == ()
