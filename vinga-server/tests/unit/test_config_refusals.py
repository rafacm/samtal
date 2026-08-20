"""Which refusal is which, by type.

The CLI prints every refusal as a sentence and needs no distinction.
The REST API answers with a status code, and the plan fixes the mapping:
a missing entity is 404, a lock that did not arrive is 409, unreadable
stored state is 500, and everything else (shape, references, slots,
stage names) is 422. Mapping by reading the message would make the
wording load-bearing, so the refusals carry types instead.

All three subclass ConfigError, so nothing that catches ConfigError
changes; these tests pin the subtype where the status code depends on
it, and pin that the messages did not move.

The busy case is forced rather than hoped for: a real lock is held by a
second connection while the busy timeout is short, once inside the
open-and-migrate step (which the API is on the path of for every
request) and once inside a repository write.
"""

import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import update

from tests.support.stores import planted
from vinga_server import db as db_module
from vinga_server.config.entities import (
    NO_SUCH_AGENT,
    NO_SUCH_DEVICE,
    NO_SUCH_FRAGMENT,
    NO_SUCH_MCP_SERVER,
    NO_SUCH_PROVIDER,
)
from vinga_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    StorageError,
    UnknownEntityError,
)
from vinga_server.config.secrets import SecretLocation, generate_key, load_keys
from vinga_server.config.store import ConfigStore
from vinga_server.db import DATABASE_FILENAME, open_database, schema

CLAUDE = SecretLocation.provider("llm", "claude", "api_key")

# Short enough that a blocked writer gives up inside a test run, and
# long enough that an unblocked one never sees it.
SHORT_BUSY_MS = 200


@pytest.fixture
def store(tmp_path: Path):
    engine = open_database(tmp_path / "db")
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
    """The 404 set, each in the fixed sentence its section answers with:
    the section and the fact, and never the identity that was asked for
    (#132). The two secret paths meet the same sentences, since what is
    not there is the entity the slot would hang on."""
    _populate(store)
    cases = [
        (lambda: store.delete_provider("llm", "gone"), NO_SUCH_PROVIDER),
        (lambda: store.delete_mcp_server("gone"), NO_SUCH_MCP_SERVER),
        (lambda: store.delete_prompt_fragment("gone"), NO_SUCH_FRAGMENT),
        (lambda: store.delete_agent("gone"), NO_SUCH_AGENT),
        (lambda: store.delete_device("aa:bb:cc:dd:ee:ff"), NO_SUCH_DEVICE),
        (
            lambda: store.clear_secret(CLAUDE),
            "providers: no secret is stored for that slot",
        ),
        (
            lambda: store.set_secret(SecretLocation.provider("llm", "gone", "api_key"), "x"),
            NO_SUCH_PROVIDER,
        ),
        (
            lambda: store.set_secret(SecretLocation.mcp_server("gone", "env.TOKEN"), "x"),
            NO_SUCH_MCP_SERVER,
        ),
    ]
    for call, message in cases:
        with pytest.raises(UnknownEntityError) as caught:
            call()
        assert str(caught.value) == message


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
    """The 500 set: the request was fine, the stored row is not."""
    _populate(store)
    planted(store, update(schema.providers).values(options="not an object"))

    with pytest.raises(StorageError) as caught:
        store.load()

    assert "the options column does not hold an object with string keys" in str(caught.value)


@pytest.mark.parametrize(
    ("table", "values"),
    [
        (schema.providers, {"type": ""}),
        (schema.mcp_servers, {"transport": "nonsense"}),
        (schema.agents, {"llm": ""}),
        (schema.agent_defaults, {"tts": ""}),
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The storage type is a ConfigError, so the boot path prints what
    it always printed rather than growing a second failure shape."""
    from vinga_server.config.boot import load_boot_config

    directory = tmp_path / "db"
    engine = open_database(directory)
    try:
        ConfigStore(engine).set_agent("sam", {"prompt": "hello"})
        with engine.begin() as connection:
            connection.execute(update(schema.agents).values(llm=""))
    finally:
        engine.dispose()
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", str(directory))

    with pytest.raises(ConfigError) as caught:
        load_boot_config()

    assert isinstance(caught.value, StorageError)
    assert "cannot be read as configuration" in str(caught.value)


def test_a_stored_row_naming_no_stage_is_a_storage_error(store: ConfigStore) -> None:
    _populate(store)
    planted(store, update(schema.providers).values(stage="nonsense"))

    with pytest.raises(StorageError):
        store.load()


def test_an_unopenable_directory_is_a_storage_error(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")

    with pytest.raises(StorageError):
        open_database(blocker / "db")


def _hold_the_write_lock(directory: Path) -> sqlite3.Connection:
    """A second process's write transaction, as far as the engine under
    test can tell: one connection to the same file, in a transaction
    that has taken the write lock and does not let go."""
    holder = sqlite3.connect(directory / DATABASE_FILENAME, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    return holder


def test_a_write_that_cannot_take_the_lock_is_a_busy_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db_module, "BUSY_TIMEOUT_MS", SHORT_BUSY_MS)
    directory = tmp_path / "db"
    engine = open_database(directory)
    holder = _hold_the_write_lock(directory)
    try:
        with pytest.raises(DatabaseBusyError) as caught:
            ConfigStore(engine).set_agent("sam", {"prompt": "hello"})
    finally:
        holder.close()
        engine.dispose()

    # The retryable sentence, unchanged: it is what the CLI prints and
    # what the API's 409 carries.
    assert "Nothing was changed; run the command again." in str(caught.value)


def test_no_database_refusal_carries_the_library_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both phases of a request's database access, and both links of the
    chain. A SQLAlchemy error holds the statement it failed on together
    with the parameters bound to it, so a refusal that kept it attached
    would carry them wherever it went: `from exc` says so outright, and
    `from None` only stops the default traceback printer from saying
    it."""
    monkeypatch.setattr(db_module, "BUSY_TIMEOUT_MS", SHORT_BUSY_MS)
    directory = tmp_path / "db"
    engine = open_database(directory)
    holder = _hold_the_write_lock(directory)
    try:
        with pytest.raises(ConfigError) as write:
            ConfigStore(engine).set_agent("sam", {"prompt": "hello"})
        with pytest.raises(ConfigError) as opening:
            open_database(directory)
    finally:
        holder.close()
        engine.dispose()

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError) as unwritable:
        open_database(blocker / "db")

    for caught in (write, opening, unwritable):
        assert caught.value.__cause__ is None, caught.value
        assert caught.value.__context__ is None, caught.value
    # And the statement the driver was running is not in the message
    # either, only the driver's own line about what went wrong.
    assert "BEGIN IMMEDIATE" not in str(opening.value)
    assert "[SQL:" not in str(opening.value)


def test_an_unusable_key_carries_no_library_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the library was handed is the key material."""
    monkeypatch.setenv("VINGA_MASTER_KEY", "not-a-fernet-key")

    with pytest.raises(ConfigError) as caught:
        load_keys()

    assert "not-a-fernet-key" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_an_open_that_cannot_take_the_lock_is_a_busy_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case the API adds: opening the database is on every request
    path, and its migration check takes the same write lock, so a lock
    timeout here has to be the retryable refusal too rather than the
    generic one."""
    monkeypatch.setattr(db_module, "BUSY_TIMEOUT_MS", SHORT_BUSY_MS)
    directory = tmp_path / "db"
    open_database(directory).dispose()
    holder = _hold_the_write_lock(directory)
    try:
        with pytest.raises(DatabaseBusyError) as caught:
            open_database(directory)
    finally:
        holder.close()

    assert "server.database.dir" in str(caught.value)
