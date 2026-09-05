"""Which refusal is which, by type.

The CLI prints every refusal as a sentence and needs no distinction.
The REST API answers with a status code, and the plan fixes the mapping:
a missing entity is 404, a lock that did not arrive is 409, unreadable
stored state is 500, and everything else (shape, references, slots,
stage names) is 422. Mapping by reading the message would make the
wording load-bearing, so the refusals carry types instead.

All three subclass ConfigError, so nothing that catches ConfigError
changes; these tests pin the subtype where the status code depends on
it, and the section a refusal is about, which is the half an operator
acts on. The sentence around that is the repository's to choose and is
deliberately not pinned here.

The busy case is forced rather than hoped for: a second connection
holds the domain chain's advisory lock while the lock timeout is short,
once inside the open-and-migrate step (which the API is on the path of
for every request) and once inside a repository write.
"""

import pytest
from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import insert, update

from tests.support.stores import holding_the_write_lock, planted, the_lock_held
from vinga_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    StorageError,
    UnknownEntityError,
)
from vinga_server.config.models import DatabaseConfig
from vinga_server.config.secrets import SecretLocation, generate_key, load_keys
from vinga_server.config.store import ConfigStore
from vinga_server.db import MIGRATION_BUSY, UNREACHABLE, open_database, schema

CLAUDE = SecretLocation.provider("llm", "claude", "api_key")


@pytest.fixture
def store():
    engine = open_database(DatabaseConfig())
    try:
        yield ConfigStore(engine, MultiFernet([Fernet(generate_key())]))
    finally:
        engine.dispose()


def _populate(store: ConfigStore) -> None:
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
    store.set_mcp_server("weather", {"transport": "stdio", "command": "weather-mcp"})
    store.set_agent("sam", {"prompt": "hello"})


def test_every_typed_refusal_is_still_a_config_error() -> None:
    for kind in (UnknownEntityError, DatabaseBusyError, StorageError):
        assert issubclass(kind, ConfigError)


def test_a_missing_entity_is_an_unknown_entity_error(store: ConfigStore) -> None:
    """The 404 set, each naming the section it is about and never the
    identity that was asked for (#132). The two secret paths land in the
    same set, since what is not there is the entity the slot would hang
    on."""
    _populate(store)
    gone = "gone"
    gone_mac = "aa:bb:cc:dd:ee:ff"
    cases = [
        (lambda: store.delete_provider("llm", gone), "providers", gone),
        (lambda: store.delete_mcp_server(gone), "mcp_servers", gone),
        (lambda: store.delete_prompt_fragment(gone), "prompt_fragments", gone),
        (lambda: store.delete_agent(gone), "agents", gone),
        (lambda: store.delete_device(gone_mac), "devices", gone_mac),
        # The slot that holds no secret, which is the one case in this
        # set where the entity exists and the credential does not.
        (lambda: store.clear_secret(CLAUDE), "providers", "api_key"),
        (
            lambda: store.set_secret(SecretLocation.provider("llm", gone, "api_key"), "x"),
            "providers",
            gone,
        ),
        (
            lambda: store.set_secret(SecretLocation.mcp_server(gone, "env.TOKEN"), "x"),
            "mcp_servers",
            gone,
        ),
    ]
    for call, section, identity in cases:
        with pytest.raises(UnknownEntityError) as caught:
            call()
        refusal = str(caught.value)
        assert refusal.startswith(f"{section}:"), refusal
        assert identity not in refusal, refusal


def test_a_refusal_that_is_not_about_a_missing_entity_stays_plain(store: ConfigStore) -> None:
    """The 422 set, asserted as "not one of the other three": a fragment
    that does not validate, a reference that would be left unresolved, a
    slot that is not a credential slot, and a stage that is not a
    stage."""
    _populate(store)
    calls = [
        lambda: store.set_provider("llm", "broken", {"type": ""}),
        lambda: store.set_agent("sam", {"prompt": "hello", "llm": "missing"}),
        lambda: store.set_secret(SecretLocation.provider("llm", "claude", "model"), "x"),
        lambda: store.set_provider("nonsense", "x", {"type": "anthropic"}),
    ]
    for call in calls:
        with pytest.raises(ConfigError) as caught:
            call()
        assert type(caught.value) is ConfigError, caught.value


def test_a_column_that_cannot_be_read_is_a_storage_error(store: ConfigStore) -> None:
    """The 500 set: the request was fine, the stored row is not. The
    columns that can still be the wrong shape are the ones holding a JSON
    value rather than a dumped model, `secrets` among them."""
    _populate(store)
    planted(store, update(schema.providers).values(secrets="not an object"))

    with pytest.raises(StorageError) as caught:
        store.load()

    assert "the secrets column does not hold an object with string keys" in str(caught.value)


@pytest.mark.parametrize(
    ("table", "values"),
    [
        (schema.providers, {"body": '{"type": ""}'}),
        (schema.mcp_servers, {"body": '{"transport": "nonsense"}'}),
        (schema.agents, {"body": '{"llm": ""}'}),
        (schema.agent_defaults, {"body": '{"tts": ""}'}),
        (schema.devices, {"mac": "not-a-mac"}),
    ],
)
def test_a_stored_row_that_will_not_validate_is_a_storage_error(
    store: ConfigStore, table: object, values: dict[str, object]
) -> None:
    """The same models, two refusals: a fragment that does not validate
    is the caller's mistake (422), a stored row that does not validate
    is not (500). Nothing the reader of a row can do makes it valid."""
    _populate(store)
    store.set_agent_defaults({"llm": "claude"})
    store.bind_device("aa:bb:cc:dd:ee:ff", ["sam"])
    planted(store, update(table).values(**values))

    with pytest.raises(StorageError) as caught:
        store.load()

    # One row names itself, the assembly of them names the whole; both
    # say what the situation is rather than what the caller did.
    assert "cannot be read" in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_an_unreadable_row_still_fails_the_boot_as_a_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The storage type is a ConfigError, so the boot path prints what
    it always printed rather than growing a second failure shape."""
    from vinga_server.config.boot import load_boot_config

    settings = DatabaseConfig()
    engine = open_database(settings)
    try:
        ConfigStore(engine).set_agent("sam", {"prompt": "hello"})
        with engine.begin() as connection:
            connection.execute(update(schema.agents).values(body='{"llm": ""}'))
    finally:
        engine.dispose()
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.setenv("VINGA_DB_NAME", settings.name)

    with pytest.raises(ConfigError) as caught:
        load_boot_config()

    assert isinstance(caught.value, StorageError)
    assert "cannot be read as configuration" in str(caught.value)


def test_a_stored_row_naming_no_stage_is_a_storage_error(store: ConfigStore) -> None:
    _populate(store)
    planted(store, update(schema.providers).values(stage="nonsense"))

    with pytest.raises(StorageError):
        store.load()


def test_two_spellings_of_one_stored_mac_are_two_rows_and_are_refused(
    store: ConfigStore,
) -> None:
    """The `devices` table is keyed by the column as written, so one MAC
    spelled two ways is two rows, and a load that quietly kept the last
    of them would drop a binding an operator can still see in the
    database.

    Pinned because the reader now makes each MAC canonical before it
    composes a location from it (#382), and the tempting next step,
    keying the composed mapping by that canonical form too, swallows
    this pair: the duplicate is found by the model's own walk, which
    only sees a duplicate while the two keys are still spelled
    differently.
    """
    _populate(store)
    store.bind_device("aa:bb:cc:dd:ee:ff", ["sam"])
    planted(
        store,
        insert(schema.devices).values(mac="AA-BB-CC-DD-EE-FF", agents=["sam"]),
    )

    with pytest.raises(StorageError) as caught:
        store.load()

    assert "appears more than once" in str(caught.value)


def test_an_unreachable_instance_is_a_storage_error() -> None:
    with pytest.raises(StorageError):
        open_database(DatabaseConfig(port=1))


def test_a_write_that_cannot_take_the_lock_is_a_busy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with holding_the_write_lock(monkeypatch):
        engine = open_database(DatabaseConfig())
        try:
            with the_lock_held(), pytest.raises(DatabaseBusyError) as caught:
                ConfigStore(engine).set_agent("sam", {"prompt": "hello"})
        finally:
            engine.dispose()

    # The retryable sentence, unchanged: it is what the CLI prints and
    # what the API's 409 carries.
    assert "Nothing was changed; run the command again." in str(caught.value)


def test_no_database_refusal_carries_the_library_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both phases of a request's database access, and both links of the
    chain. A SQLAlchemy error holds the statement it failed on together
    with the parameters bound to it, and a psycopg connection error
    quotes the DSN it tried, password and all, so a refusal that kept
    either attached would carry them wherever it went: `from exc` says
    so outright, and `from None` only stops the default traceback
    printer from saying it."""
    with holding_the_write_lock(monkeypatch):
        engine = open_database(DatabaseConfig())
        try:
            with the_lock_held():
                with pytest.raises(ConfigError) as write:
                    ConfigStore(engine).set_agent("sam", {"prompt": "hello"})
                with pytest.raises(ConfigError) as opening:
                    open_database(DatabaseConfig())
        finally:
            engine.dispose()

    with pytest.raises(ConfigError) as unreachable:
        open_database(DatabaseConfig(port=1))

    for caught in (write, opening, unreachable):
        assert caught.value.__cause__ is None, caught.value
        assert caught.value.__context__ is None, caught.value
    # And the statement the driver was running is not in the message
    # either, nor anything of the connection it was running on.
    assert "pg_advisory_xact_lock" not in str(opening.value)
    assert "[SQL:" not in str(opening.value)
    assert "127.0.0.1" not in str(unreachable.value)


def test_an_unusable_key_carries_no_library_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the library was handed is the key material."""
    monkeypatch.setenv("VINGA_MASTER_KEY", "not-a-fernet-key")

    with pytest.raises(ConfigError) as caught:
        load_keys()

    assert "not-a-fernet-key" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_an_open_that_cannot_take_the_lock_is_a_busy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case the API adds: opening the database is on every request
    path, and its migration check takes the same advisory lock, so a
    lock timeout here has to be the retryable refusal too rather than
    the generic one."""
    with holding_the_write_lock(monkeypatch):
        open_database(DatabaseConfig()).dispose()
        with the_lock_held(), pytest.raises(DatabaseBusyError) as caught:
            open_database(DatabaseConfig())

    assert str(caught.value) == MIGRATION_BUSY
    assert str(caught.value) != UNREACHABLE
