"""Opening the domain configuration database: creation, migration, reopen.

The database is opened by the server at boot and by every CLI
invocation, so the interesting cases are the ones that happen without
anybody watching: an empty data volume, a file that is already current,
and two processes doing either at the same moment.
"""

import os
import threading
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

import samtal_server
from samtal_server.config import ConfigError
from samtal_server.db import DATABASE_FILENAME, open_database

EXPECTED_TABLES = {
    "providers",
    "mcp_servers",
    "agent_defaults",
    "agents",
    "devices",
    "domain_settings",
}


def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _version(engine) -> list[str]:
    with engine.connect() as connection:
        return [row[0] for row in connection.execute(text("select * from alembic_version"))]


def test_fresh_directory_gains_a_migrated_database(tmp_path: Path) -> None:
    directory = tmp_path / "db"

    engine = open_database(directory)
    try:
        assert (directory / DATABASE_FILENAME).is_file()
        assert EXPECTED_TABLES <= _tables(engine)
        assert len(_version(engine)) == 1
    finally:
        engine.dispose()


def test_the_connection_is_configured_for_concurrent_use(tmp_path: Path) -> None:
    """WAL and a busy timeout are what let a CLI write land while the
    server holds the same file open."""
    engine = open_database(tmp_path / "db")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert connection.execute(text("PRAGMA busy_timeout")).scalar() > 0
    finally:
        engine.dispose()


def test_an_already_migrated_database_reopens(tmp_path: Path) -> None:
    directory = tmp_path / "db"

    first = open_database(directory)
    version = _version(first)
    first.dispose()

    second = open_database(directory)
    try:
        assert EXPECTED_TABLES <= _tables(second)
        assert _version(second) == version
    finally:
        second.dispose()


def test_concurrent_openers_serialize_on_the_migration_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first opener is held inside the migration after its
    transaction has taken the write lock; the second is started and
    must not reach the migration until the first commits. That pins
    the serialization property directly, rather than hoping the
    scheduler produces the race: BEGIN IMMEDIATE precedes Alembic's
    version-table read, so the loser reads the schema the winner
    committed and finds it current instead of creating the same
    tables twice. Without the lock, the second opener enters the
    migration while the first still holds it, and the ordering
    assertion below fails."""
    from samtal_server import db as db_module

    directory = tmp_path / "db"
    real_upgrade = db_module.command.upgrade
    first_inside = threading.Event()
    release_first = threading.Event()
    entered: list[int] = []
    entered_lock = threading.Lock()
    failures: list[BaseException] = []
    engines = []

    def gated_upgrade(config, revision) -> None:
        with entered_lock:
            ordinal = len(entered)
            entered.append(ordinal)
        if ordinal == 0:
            first_inside.set()
            assert release_first.wait(timeout=30), "the first opener was never released"
        real_upgrade(config, revision)

    monkeypatch.setattr(db_module.command, "upgrade", gated_upgrade)

    def opener() -> None:
        try:
            engines.append(open_database(directory))
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            failures.append(exc)

    first = threading.Thread(target=opener)
    first.start()
    assert first_inside.wait(timeout=30), "the first opener never reached the migration"

    second = threading.Thread(target=opener)
    second.start()
    # The second opener must park on the write lock, outside the
    # migration, for as long as the first holds it. The window is long
    # enough to catch an unserialized entry and far inside the busy
    # timeout, so a correctly parked opener neither enters nor fails.
    second.join(timeout=1.0)
    assert second.is_alive(), "the second opener finished while the first held the lock"
    with entered_lock:
        assert entered == [0], "the second opener entered the migration behind the lock"

    release_first.set()
    first.join(timeout=60)
    second.join(timeout=60)

    try:
        assert not first.is_alive() and not second.is_alive()
        assert not failures, failures
        assert len(engines) == 2
        assert len(entered) == 2
        for engine in engines:
            assert EXPECTED_TABLES <= _tables(engine)
            assert len(_version(engine)) == 1
    finally:
        for engine in engines:
            engine.dispose()


def test_an_uncreatable_directory_names_the_configuration_key(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        open_database(blocker / "db")

    assert "server.database.dir" in str(caught.value)


@pytest.mark.skipif(os.getuid() == 0, reason="root writes to unwritable directories")
def test_an_unwritable_directory_names_the_configuration_key(tmp_path: Path) -> None:
    directory = tmp_path / "read-only"
    directory.mkdir()
    directory.chmod(0o500)
    try:
        with pytest.raises(ConfigError) as caught:
            open_database(directory)
    finally:
        directory.chmod(0o700)

    assert "server.database.dir" in str(caught.value)


def test_provider_rows_hold_every_declared_model_field(tmp_path: Path) -> None:
    """ProviderConfig's declared fields are excluded from its options
    property, so each needs a column of its own; a missing one would
    make the repository silently drop that field on every round trip.
    api_key_env is the case that bit: the environment-reference
    credential form every cloud provider uses."""
    from samtal_server.config.models import ProviderConfig
    from samtal_server.db.schema import providers

    declared = set(ProviderConfig.model_fields)
    assert declared <= set(providers.c.keys())

    engine = open_database(tmp_path / "db")
    try:
        with engine.connect() as connection:
            connection.execute(
                providers.insert().values(
                    stage="llm",
                    name="claude",
                    type="anthropic",
                    api_key_env="ANTHROPIC_API_KEY",
                    egress=None,
                    options={"model": "claude-sonnet-5"},
                    secrets={},
                )
            )
            connection.commit()
        with engine.connect() as connection:
            row = connection.execute(providers.select()).mappings().one()
        assert row["api_key_env"] == "ANTHROPIC_API_KEY"
        assert row["options"] == {"model": "claude-sonnet-5"}
    finally:
        engine.dispose()


def test_the_migrations_ship_inside_the_package() -> None:
    """Discovery from an installed wheel is proved in CI, which installs
    one and migrates from it. This is the cheap half: the scripts are
    inside the package directory hatchling builds, not beside it."""
    package = Path(samtal_server.__file__).resolve().parent
    migrations = package / "db" / "migrations"

    assert (migrations / "env.py").is_file()
    assert list((migrations / "versions").glob("*.py"))
