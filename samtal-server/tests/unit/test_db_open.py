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


def test_concurrent_openers_do_not_race_the_baseline_migration(tmp_path: Path) -> None:
    """Both openers see an empty file and both decide to migrate it. The
    write lock the upgrade takes before it reads the version table is
    what makes the loser find the schema already current instead of
    creating the same tables twice."""
    directory = tmp_path / "db"
    start = threading.Barrier(2)
    failures: list[BaseException] = []
    engines = []

    def opener() -> None:
        start.wait(timeout=10)
        try:
            engines.append(open_database(directory))
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            failures.append(exc)

    threads = [threading.Thread(target=opener) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    try:
        assert not failures, failures
        assert len(engines) == 2
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


def test_the_migrations_ship_inside_the_package() -> None:
    """Discovery from an installed wheel is proved in CI, which installs
    one and migrates from it. This is the cheap half: the scripts are
    inside the package directory hatchling builds, not beside it."""
    package = Path(samtal_server.__file__).resolve().parent
    migrations = package / "db" / "migrations"

    assert (migrations / "env.py").is_file()
    assert list((migrations / "versions").glob("*.py"))
