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

import vinga_server
from vinga_server.config import ConfigError
from vinga_server.db import DATABASE_FILENAME, open_database

EXPECTED_TABLES = {
    "providers",
    "mcp_servers",
    "prompt_fragments",
    "agent_defaults",
    "agents",
    "devices",
    "domain_settings",
}

# The body column on each entity table, which is where every non-key
# field of that entity lives (#243). A chain that did not create them
# fails here rather than at the first write on a deployment. The same
# set the installed-wheel check in CI holds, and the two move together.
EXPECTED_COLUMNS = {
    "providers": {"stage", "name", "body", "secrets"},
    "mcp_servers": {"name", "body", "secrets"},
    "prompt_fragments": {"name", "body"},
    "agent_defaults": {"id", "body"},
    "agents": {"name", "body"},
}

# The head of the packaged domain chain, which is one revision and no
# longer a chain at all. A database stamped at anything else is one this
# build cannot upgrade, and the refusal below is what it gets told.
HEAD = "2001_json_body_baseline"


def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _columns(engine, table: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table)}


def _version(engine) -> list[str]:
    with engine.connect() as connection:
        return [row[0] for row in connection.execute(text("select * from alembic_version"))]


def test_fresh_directory_gains_a_migrated_database(tmp_path: Path) -> None:
    directory = tmp_path / "db"

    engine = open_database(directory)
    try:
        assert (directory / DATABASE_FILENAME).is_file()
        assert EXPECTED_TABLES <= _tables(engine)
        assert _version(engine) == [HEAD]
        for table, columns in EXPECTED_COLUMNS.items():
            assert _columns(engine, table) == columns
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
    from vinga_server import db as db_module

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


# The property `test_provider_rows_hold_every_declared_model_field` used
# to state, that every declared field of `ProviderConfig` has somewhere
# in the row to live, is inherited by
# `test_config_bodies.py::test_a_body_round_trips_through_the_mapper_pair`.
# It is not a check any more but a consequence: the row holds the model's
# own dump, so a field cannot be missing from it without the dump being
# missing it too, and the round trip is what says so for every kind
# rather than for the one that had the most columns.


# The databases this build cannot open
#
# The domain chain was squashed to one baseline under a revision id
# nothing was ever stamped with (#243), so a database from the old chain
# is not silently at head: Alembic cannot find its revision. What an
# operator meets is the sentence below rather than a traceback, and it is
# the whole of the operator-facing surface of "these are unsupported".


def _stamped(directory: Path, revision: str) -> None:
    """A migrated database, then rewritten to say it is at `revision`,
    which is what a deployment carrying another build's chain has."""
    engine = open_database(directory)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("update alembic_version set version_num = :revision"),
                {"revision": revision},
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("revision", ["0001", "0002", "0003", "0004"])
def test_a_database_from_the_squashed_chain_says_to_reset(
    tmp_path: Path, revision: str
) -> None:
    """Every revision the squash deleted, one by one, because the set is
    closed and naming it is what makes the arm narrow."""
    directory = tmp_path / "db"
    _stamped(directory, revision)

    with pytest.raises(ConfigError) as caught:
        open_database(directory)

    problem = str(caught.value)
    assert "a revision this build does not carry" in problem
    assert "Reset the database directory and re-seed" in problem
    # The stored revision is a value in a file nothing here validates, so
    # it is not quoted back, and neither is Alembic's own sentence.
    assert revision not in problem
    assert "Can\'t locate" not in problem


def test_the_domain_database_answers_only_for_its_own_deleted_revisions(
    tmp_path: Path,
) -> None:
    """The other direction of the same seam: `0001` is a live revision of
    the conversations chain and a deleted one of this chain, and what
    decides is which chain was opened rather than the id alone."""
    directory = tmp_path / "db"
    _stamped(directory, "0001")

    with pytest.raises(ConfigError) as caught:
        open_database(directory)

    assert "Reset the database directory and re-seed" in str(caught.value)


def test_a_database_from_a_newer_build_is_not_told_to_delete_itself(
    tmp_path: Path,
) -> None:
    """The rollback: a deployment upgraded, then rolled back to this
    image, so the volume is stamped at a revision from a chain this build
    does not have yet.

    Alembic fails it exactly as it fails a database from the squashed
    chain, and the two want opposite things. This one is current: the
    remedy is to roll forward to the build that wrote it, and telling its
    operator to reset the volume would destroy a live configuration. So
    it takes the ordinary migration-failure sentence instead, which is
    what it took before the squash arm existed.
    """
    directory = tmp_path / "db"
    _stamped(directory, "2002_a_migration_this_build_does_not_have")

    with pytest.raises(ConfigError) as caught:
        open_database(directory)

    problem = str(caught.value)
    assert "Reset the database directory" not in problem
    assert "before the storage reshape" not in problem
    assert "cannot migrate the database" in problem
    assert "server.database.dir" in problem


def test_the_conversations_database_is_never_told_the_domain_sentence(
    tmp_path: Path,
) -> None:
    """The two databases share every line of the opening machinery and
    must not share this one sentence.

    The conversations chain deleted nothing, so no database of its can be
    stranded by a squash, and the domain's advice is about the other
    file: it would send whoever met an unreadable conversations database
    to reset a volume and re-seed a configuration, destroying recorded
    conversations the store is otherwise careful to erase physically only
    when asked.
    """
    from vinga_server.conversations.store import open_conversations

    directory = tmp_path / "db"
    engine = open_conversations(directory)
    try:
        with engine.begin() as connection:
            # `0001` is a revision the conversations chain really has, so
            # this is the domain's superseded set met on the other
            # database: the id that would trip the arm if the answer were
            # shared instead of supplied.
            connection.execute(
                text("update alembic_version set version_num = '0001_not_this_chain'")
            )
    finally:
        engine.dispose()

    with pytest.raises(ConfigError) as caught:
        open_conversations(directory)

    problem = str(caught.value)
    assert "Reset the database directory" not in problem
    assert "re-seed the configuration" not in problem
    assert "cannot migrate the database" in problem


def test_an_alembic_failure_that_is_not_a_stranded_database_is_not_told_to_reset(
    tmp_path: Path,
) -> None:
    """The other CommandError this used to answer with the reset
    sentence: a script directory Alembic cannot read at all. Nothing
    about the database is wrong, so nothing about the database is the
    remedy."""
    from vinga_server.db import migration_failure, open_at

    with pytest.raises(ConfigError) as caught:
        open_at(tmp_path / "db", "vinga.db", tmp_path / "no-such-migrations")

    assert "Reset the database directory" not in str(caught.value)

    # And the shape directly, since a resolution failure with no cause at
    # all is a thing a future Alembic could produce.
    from alembic.util.exc import CommandError

    plain = migration_failure(CommandError("multiple heads"), tmp_path / "vinga.db")
    assert "Reset the database directory" not in str(plain)


def test_the_migrations_ship_inside_the_package() -> None:
    """Discovery from an installed wheel is proved in CI, which installs
    one and migrates from it. This is the cheap half: the scripts are
    inside the package directory hatchling builds, not beside it."""
    package = Path(vinga_server.__file__).resolve().parent
    migrations = package / "db" / "migrations"

    assert (migrations / "env.py").is_file()
    assert list((migrations / "versions").glob("*.py"))
