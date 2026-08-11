"""The repository: what it loads, what it writes, and what it refuses.

Two properties carry most of this file. Every reference resolves after
every write, which is what makes the database always loadable by a
server; and no completeness rule is enforced at write time, which is
what lets a deployment be built up from nothing in the natural order
without the first agent and default_agent deadlocking on each other.
"""

import threading
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import update

from samtal_server.config import ConfigError
from samtal_server.config.secrets import SecretLocation, generate_key
from samtal_server.config.store import ConfigStore, verify_secrets
from samtal_server.db import open_database, schema

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"

CLAUDE = SecretLocation.provider("llm", "claude", "api_key")
WEATHER = SecretLocation.mcp_server("weather", "headers.Authorization")


def _chain(exc: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current)]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


@pytest.fixture
def keys() -> MultiFernet:
    return MultiFernet([Fernet(generate_key())])


@pytest.fixture
def store(tmp_path: Path, keys: MultiFernet):
    engine = open_database(tmp_path / "db")
    try:
        yield ConfigStore(engine, keys)
    finally:
        engine.dispose()


def _populate(store: ConfigStore) -> None:
    """A working configuration, written in the natural order."""
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
    store.set_provider("asr", "whisper", {"type": "faster_whisper", "model": "small"})
    store.set_provider("tts", "voice", {"type": "piper", "model": "es"})
    store.set_provider("vad", "silero", {"type": "silero"})
    store.set_mcp_server(
        "home",
        {"transport": "stdio", "command": "uvx", "args": ["home-mcp"], "egress": False},
    )
    store.set_agent_defaults(
        {"llm": "claude", "asr": "whisper", "tts": "voice", "vad": "silero", "mcp": ["home"]}
    )
    store.set_agent("sam", {"prompt": "You are Sam."})
    store.bind_device("AA-BB-CC-DD-EE-FF", ["sam"])
    store.set_default_agent("sam")


def test_an_empty_database_loads_an_empty_snapshot(store: ConfigStore) -> None:
    snapshot = store.load()

    assert snapshot.domain.agents == {}
    assert snapshot.domain.default_agent is None
    assert snapshot.domain.providers.llm == {}
    assert len(snapshot.secrets) == 0


def test_a_configuration_round_trips_through_the_rows(store: ConfigStore) -> None:
    _populate(store)
    store.set_mcp_server(
        "weather",
        {
            "transport": "streamable_http",
            "url": "https://example.invalid/mcp",
            "headers": {"Authorization": "$WEATHER_TOKEN"},
            "tool_timeout_s": 5,
        },
    )
    store.set_agent(
        "poet",
        {
            "prompt": "You are a poet.",
            "tts": "voice",
            "mcp": [],
            "filler": {"enabled": True, "phrases": ["Hmm..."]},
        },
    )

    domain = store.load().domain

    assert domain.providers.llm["claude"].type == "anthropic"
    assert domain.providers.llm["claude"].options == {"model": "claude-sonnet-5"}
    assert domain.mcp_servers["home"].command == "uvx"
    assert domain.mcp_servers["home"].egress is False
    assert domain.mcp_servers["weather"].headers == {"Authorization": "$WEATHER_TOKEN"}
    assert domain.mcp_servers["weather"].tool_timeout_s == 5
    assert domain.agent_defaults.mcp == ["home"]
    assert domain.agents["poet"].filler is not None
    assert domain.agents["poet"].filler.phrases == ["Hmm..."]
    # A list replaces rather than extends, so an empty one is not a null.
    assert domain.agents["poet"].mcp == []
    assert domain.agents["sam"].mcp is None
    assert domain.devices == {"aa:bb:cc:dd:ee:ff": ["sam"]}
    assert domain.default_agent == "sam"


def test_a_loaded_snapshot_has_no_unresolved_references(store: ConfigStore) -> None:
    from samtal_server.config.models import check_completeness, check_references

    _populate(store)
    domain = store.load().domain

    assert check_references(domain) == []
    assert check_completeness(domain) == []


def test_the_credential_reference_lives_in_its_own_column(store: ConfigStore) -> None:
    """api_key_env is a declared model field with a column of its own
    (PR #95 review finding 1); folding it into the options JSON would
    contradict options holding exactly the model extras, and a later
    reader of the raw row would miss it."""
    store.set_provider(
        "llm",
        "claude",
        {"type": "anthropic", "model": "claude-sonnet-5", "api_key_env": "ANTHROPIC_API_KEY"},
    )

    with store._engine.connect() as connection:
        row = connection.execute(schema.providers.select()).mappings().one()
    assert row["api_key_env"] == "ANTHROPIC_API_KEY"
    assert "api_key_env" not in row["options"]

    loaded = store.load().domain.providers.llm["claude"]
    assert loaded.api_key_env == "ANTHROPIC_API_KEY"
    assert loaded.options == {"model": "claude-sonnet-5"}


def test_building_up_from_empty_never_wedges(store: ConfigStore) -> None:
    """The deadlock the write-time check set is chosen to avoid: every
    intermediate state here fails the boot-only completeness rule, and
    none of them may be refused."""
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
    store.set_agent_defaults({"llm": "claude"})
    # An agent with no default_agent naming it yet, which is the state
    # boot would refuse and a write must not.
    store.set_agent("sam", {"prompt": "You are Sam."})
    store.set_default_agent("sam")
    store.bind_device("aa:bb:cc:dd:ee:ff", ["sam"])

    assert store.load().domain.default_agent == "sam"


def test_a_stage_left_unresolved_does_not_block_a_write(store: ConfigStore) -> None:
    """An agent whose ASR resolves through neither its own entry nor the
    defaults is an unfinished deployment, not a broken entity. Provider
    construction is what refuses it, at boot."""
    store.set_agent("sam", {"prompt": "You are Sam."})

    assert store.load().domain.agents["sam"].asr is None


def test_clearing_the_default_agent_is_reachable(store: ConfigStore) -> None:
    _populate(store)

    store.clear_default_agent()

    assert store.load().domain.default_agent is None


def test_an_unknown_provider_reference_is_refused(store: ConfigStore) -> None:
    with pytest.raises(ConfigError, match='unknown llm provider "ghost"'):
        store.set_agent("sam", {"llm": "ghost"})

    assert store.load().domain.agents == {}


def test_an_unknown_mcp_reference_is_refused(store: ConfigStore) -> None:
    with pytest.raises(ConfigError, match='unknown MCP server "home"'):
        store.set_agent_defaults({"mcp": ["home"]})


def test_binding_a_device_to_an_unknown_agent_is_refused(store: ConfigStore) -> None:
    with pytest.raises(ConfigError, match='agent "ghost" is not a defined agent'):
        store.bind_device("aa:bb:cc:dd:ee:ff", ["ghost"])

    assert store.load().domain.devices == {}


def test_an_unknown_default_agent_is_refused(store: ConfigStore) -> None:
    with pytest.raises(ConfigError, match='default_agent "ghost" is not a defined agent'):
        store.set_default_agent("ghost")


def test_deleting_a_referenced_provider_is_refused(store: ConfigStore) -> None:
    _populate(store)

    with pytest.raises(ConfigError, match="unknown llm provider"):
        store.delete_provider("llm", "claude")

    assert "claude" in store.load().domain.providers.llm


def test_deleting_a_referenced_mcp_server_is_refused(store: ConfigStore) -> None:
    _populate(store)

    with pytest.raises(ConfigError, match="unknown MCP server"):
        store.delete_mcp_server("home")


def test_deleting_an_agent_a_device_is_bound_to_is_refused(store: ConfigStore) -> None:
    _populate(store)
    store.clear_default_agent()

    with pytest.raises(ConfigError, match='devices.aa:bb:cc:dd:ee:ff: agent "sam"'):
        store.delete_agent("sam")


def test_deleting_the_default_agent_is_refused(store: ConfigStore) -> None:
    _populate(store)
    store.delete_device("aa:bb:cc:dd:ee:ff")

    with pytest.raises(ConfigError, match='default_agent "sam" is not a defined agent'):
        store.delete_agent("sam")

    # Unbound and undefaulted, the same agent goes.
    store.clear_default_agent()
    store.delete_agent("sam")
    assert store.load().domain.agents == {}


def test_an_unfreed_entity_is_named_when_it_does_not_exist(store: ConfigStore) -> None:
    for call in (
        lambda: store.delete_provider("llm", "ghost"),
        lambda: store.delete_mcp_server("ghost"),
        lambda: store.delete_agent("ghost"),
        lambda: store.delete_device("aa:bb:cc:dd:ee:ff"),
    ):
        with pytest.raises(ConfigError, match="no such"):
            call()


def test_an_invalid_fragment_is_refused_without_quoting_it(store: ConfigStore) -> None:
    """The refusal names the key and the rule, and carries nothing of
    the fragment: not in the message, and not in the exception chain
    either.

    Both links are asserted, because clearing only the cause is not
    enough. An exception raised inside a handler keeps the one being
    handled as its __context__, and a pydantic ValidationError's
    errors() hold the complete rejected input, secret and all; its
    str() happens to truncate the middle of a long value, which is
    luck rather than a property to rely on."""
    with pytest.raises(ConfigError) as caught:
        store.set_provider("llm", "claude", {"type": "anthropic", "api_key": SECRET})

    message = str(caught.value)
    assert "providers.llm.claude" in message
    assert "api_key" in message
    assert SECRET not in _chain(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_an_unknown_stage_and_an_empty_name_are_refused(store: ConfigStore) -> None:
    with pytest.raises(ConfigError, match="not a provider stage"):
        store.set_provider("speech", "x", {"type": "mock"})
    with pytest.raises(ConfigError, match="the name is empty"):
        store.set_agent("  ", {})
    with pytest.raises(ConfigError, match="not a MAC address"):
        store.delete_device("nonsense")


def test_replacing_an_entity_keeps_its_stored_secrets(store: ConfigStore) -> None:
    """A fragment cannot carry ciphertext, so a whole-row replacement
    would erase every stored secret on an ordinary edit."""
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
    store.set_secret(CLAUDE, SECRET)

    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-haiku"})

    snapshot = store.load()
    assert snapshot.domain.providers.llm["claude"].options == {"model": "claude-haiku"}
    assert snapshot.secrets.secret(CLAUDE) == SECRET


def test_deleting_an_entity_deletes_its_stored_secrets(store: ConfigStore) -> None:
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
    store.set_secret(CLAUDE, SECRET)

    store.delete_provider("llm", "claude")

    assert store.load().secrets.locations() == []


def test_a_secret_can_be_set_and_cleared_on_both_entity_kinds(store: ConfigStore) -> None:
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
    store.set_mcp_server(
        "weather", {"transport": "streamable_http", "url": "https://example.invalid/mcp"}
    )

    store.set_secret(CLAUDE, SECRET)
    store.set_secret(WEATHER, SECRET)
    assert store.load().secrets.locations() == [WEATHER, CLAUDE]

    store.clear_secret(WEATHER)
    assert store.load().secrets.locations() == [CLAUDE]


def test_a_secret_for_an_unknown_entity_or_slot_is_refused(store: ConfigStore) -> None:
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})

    for location in (
        SecretLocation.provider("llm", "ghost", "api_key"),
        SecretLocation.provider("llm", "claude", "model"),
        SecretLocation.provider("llm", "claude", "api_key_env"),
        SecretLocation.mcp_server("ghost", "env.TOKEN"),
    ):
        with pytest.raises(ConfigError) as caught:
            store.set_secret(location, SECRET)
        assert SECRET not in _chain(caught.value)


def test_storing_a_secret_without_a_key_is_refused(tmp_path: Path) -> None:
    """The one command that needs a key. Everything else treats
    ciphertext as opaque, so the CLI stays usable as the recovery tool
    when the key is missing or wrong."""
    engine = open_database(tmp_path / "db")
    try:
        keyless = ConfigStore(engine)
        keyless.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})

        with pytest.raises(ConfigError) as caught:
            keyless.set_secret(CLAUDE, SECRET)

        assert CLAUDE.describe() in str(caught.value)
        assert SECRET not in _chain(caught.value)
    finally:
        engine.dispose()


def test_verify_secrets_passes_when_every_token_opens(store: ConfigStore) -> None:
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
    store.set_secret(CLAUDE, SECRET)

    verify_secrets(store.load().secrets)


def test_verify_secrets_names_the_entity_and_slot_it_cannot_open(
    tmp_path: Path, keys: MultiFernet
) -> None:
    engine = open_database(tmp_path / "db")
    try:
        store = ConfigStore(engine, keys)
        store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
        store.set_secret(CLAUDE, SECRET)

        # A wrong key, and no key at all: the two ways a deployment
        # arrives at a database it cannot read.
        for wrong in (MultiFernet([Fernet(generate_key())]), None):
            with pytest.raises(ConfigError) as caught:
                verify_secrets(ConfigStore(engine, wrong).load().secrets)
            assert CLAUDE.describe() in str(caught.value)
            assert SECRET not in _chain(caught.value)

        # And a token that is not a token, which is what a hand-edited
        # or half-restored database looks like.
        with engine.begin() as connection:
            connection.execute(
                update(schema.providers).values(secrets={"api_key": {"enc": "rubbish"}})
            )
        with pytest.raises(ConfigError, match=CLAUDE.describe()):
            verify_secrets(store.load().secrets)
    finally:
        engine.dispose()


def test_a_row_that_is_not_loadable_is_reported_as_a_config_error(
    tmp_path: Path, keys: MultiFernet
) -> None:
    """A hand-edited database is the case this exists for: the failure
    names the entry, and no pydantic traceback reaches the caller."""
    engine = open_database(tmp_path / "db")
    try:
        store = ConfigStore(engine, keys)
        store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
        with engine.begin() as connection:
            connection.execute(update(schema.providers).values(type=""))

        with pytest.raises(ConfigError) as caught:
            store.load()

        assert "providers.llm.claude" in str(caught.value)
        assert caught.value.__cause__ is None
    finally:
        engine.dispose()


CORRUPTIONS = [
    ("providers", "options", "not an object"),
    ("providers", "secrets", "not an object"),
    ("mcp_servers", "args", "not an array"),
    ("mcp_servers", "env", ["not", "an", "object"]),
    ("mcp_servers", "headers", "not an object"),
    ("mcp_servers", "secrets", "not an object"),
    ("agents", "mcp", "not an array"),
    ("agents", "filler", ["not", "an", "object"]),
    ("agent_defaults", "mcp", "not an array"),
    ("agent_defaults", "filler", "not an object"),
    ("devices", "agents", "sam"),
    ("domain_settings", "value", {"not": "a string"}),
]


@pytest.mark.parametrize(("table", "column", "written"), CORRUPTIONS)
def test_a_json_column_of_the_wrong_shape_is_a_config_error(
    store: ConfigStore, table: str, column: str, written: object
) -> None:
    """SQLite enforces no shape on a JSON column, so a hand-edited or
    half-restored row can hold a string where a mapping belongs. Every
    reader would then raise a TypeError or an AttributeError, which is
    neither a database error nor a validation error, and would travel
    straight through the sanitized boundary as a traceback.

    The devices case is the one that fails silently rather than loudly
    without this: iterating a string succeeds and binds the device to
    one agent per character."""
    _populate(store)
    store.set_agent("poet", {"prompt": "p", "mcp": [], "filler": {"enabled": False}})

    with store._engine.begin() as connection:
        connection.execute(update(getattr(schema, table)).values(**{column: written}))

    with pytest.raises(ConfigError) as caught:
        store.load()

    message = str(caught.value)
    assert column in message
    assert str(written) not in message
    assert caught.value.__cause__ is None


def test_a_corrupt_json_column_does_not_stop_a_secret_being_cleared(
    store: ConfigStore,
) -> None:
    """The recovery direction: a secrets column that is not an object
    still refuses in words rather than in a traceback."""
    _populate(store)

    with store._engine.begin() as connection:
        connection.execute(update(schema.providers).values(secrets="not an object"))

    with pytest.raises(ConfigError) as caught:
        store.clear_secret(CLAUDE)

    assert "secrets" in str(caught.value)
    assert caught.value.__cause__ is None


def test_two_concurrent_writers_serialize(
    tmp_path: Path, keys: MultiFernet, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One writer deletes a provider while the other writes an agent
    that references it. Whichever runs second sees the first one's
    change, so exactly one of them is refused for the reference it would
    leave unresolved, and the database never ends up holding one.

    The pacing below is what makes the failure deterministic rather than
    a race that usually does not happen: each writer announces that it
    has read the snapshot and then waits for the other to announce the
    same. Under BEGIN IMMEDIATE that wait always times out, because the
    second writer cannot have read anything while the first holds the
    write lock. Under a deferred BEGIN both read the state before either
    change, and the invariant this asserts is what breaks.
    """
    from samtal_server.config import store as store_module

    directory = tmp_path / "db"
    setup = open_database(directory)
    try:
        ConfigStore(setup, keys).set_provider("llm", "claude", {"type": "anthropic", "model": "m"})
    finally:
        setup.dispose()

    names = ("delete", "reference")
    has_read = {name: threading.Event() for name in names}
    read_domain = store_module._read_domain

    def paced(connection):
        domain = read_domain(connection)
        name = threading.current_thread().name
        if name in has_read:
            has_read[name].set()
            other = names[1] if name == names[0] else names[0]
            has_read[other].wait(timeout=0.5)
        return domain

    monkeypatch.setattr(store_module, "_read_domain", paced)

    start = threading.Barrier(2)
    outcomes: list[BaseException | None] = []
    lock = threading.Lock()

    def writer(change) -> None:
        engine = open_database(directory)
        store = ConfigStore(engine, keys)
        try:
            start.wait(timeout=10)
            try:
                change(store)
                outcome: BaseException | None = None
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                outcome = exc
            with lock:
                outcomes.append(outcome)
        finally:
            engine.dispose()

    def delete(store: ConfigStore) -> None:
        store.delete_provider("llm", "claude")

    def reference(store: ConfigStore) -> None:
        store.set_agent("sam", {"llm": "claude"})

    threads = [
        threading.Thread(target=writer, args=(change,), name=name)
        for name, change in zip(names, (delete, reference), strict=True)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert len(outcomes) == 2
    refused = [outcome for outcome in outcomes if outcome is not None]
    assert len(refused) == 1, outcomes
    assert isinstance(refused[0], ConfigError)
    # Refused for what it would have left unresolved, not for a lock it
    # could not take: the loser waited, and then read the winner's state.
    assert "references unresolved" in str(refused[0])

    engine = open_database(directory)
    try:
        from samtal_server.config.models import check_references

        assert check_references(ConfigStore(engine, keys).load().domain) == []
    finally:
        engine.dispose()
