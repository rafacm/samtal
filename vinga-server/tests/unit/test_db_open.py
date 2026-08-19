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

# The columns a migration after the baseline adds, so a chain that
# stopped early fails here rather than at the first write on a
# deployment. The same set the installed-wheel check in CI holds.
EXPECTED_COLUMNS = {
    "mcp_servers": {"instructions", "use_server_instructions", "inject_prompts"},
    "agent_defaults": {"prompt_includes"},
    "agents": {"prompt_includes"},
}


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


def test_provider_rows_hold_every_declared_model_field(tmp_path: Path) -> None:
    """ProviderConfig's declared fields are excluded from its options
    property, so each needs a column of its own; a missing one would
    make the repository silently drop that field on every round trip.
    api_key_env is the case that bit: the environment-reference
    credential form every cloud provider uses."""
    from vinga_server.config.models import ProviderConfig
    from vinga_server.db.schema import providers

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


# Upgrading a database somebody is already running
#
# Every other test here migrates an empty directory, which proves the
# scripts run and nothing about what happens to rows that were already
# there. This one builds the baseline schema, fills it the way a
# deployment fills it, and takes it to head through every migration that
# has landed since, loading the result through the repository a server
# boots on. It grows a case per migration rather than being replaced, so
# the whole chain is proven at every merge.

SEEDED_MAC = "aa:bb:cc:dd:ee:ff"


def _at_baseline(directory: Path):
    """A database at revision 0001 and no further: the schema a
    deployment that installed before this feature is running."""
    from alembic import command
    from alembic.config import Config as AlembicConfig
    from sqlalchemy import create_engine

    from vinga_server.db import _MIGRATIONS_DIR, DATABASE_FILENAME

    directory.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{directory / DATABASE_FILENAME}")
    with engine.connect() as connection:
        config = AlembicConfig()
        config.set_main_option("script_location", str(_MIGRATIONS_DIR))
        config.attributes["connection"] = connection
        command.upgrade(config, "0001")
        connection.commit()
    return engine


def _seed_baseline_rows(engine) -> None:
    """One nonempty row per table, written as SQL against the baseline
    columns rather than through the repository: the repository writes
    today's columns, and what this test is about is rows that predate
    them."""
    import json

    statements = [
        (
            "insert into providers (stage, name, type, api_key_env, egress, options, "
            "secrets) values (:stage, :name, :type, :api_key_env, :egress, :options, "
            ":secrets)",
            {
                "stage": "llm",
                "name": "claude",
                "type": "anthropic",
                "api_key_env": "ANTHROPIC_API_KEY",
                "egress": None,
                "options": json.dumps({"model": "claude-sonnet-5"}),
                "secrets": json.dumps({}),
            },
        ),
        (
            "insert into mcp_servers (name, transport, command, args, env, url, headers, "
            "egress, tool_timeout_s, secrets) values (:name, :transport, :command, :args, "
            ":env, :url, :headers, :egress, :tool_timeout_s, :secrets)",
            {
                "name": "home",
                "transport": "stdio",
                "command": "uvx",
                "args": json.dumps(["home-mcp"]),
                "env": json.dumps({"API_ACCESS_TOKEN": "$HOME_TOKEN"}),
                "url": None,
                "headers": json.dumps({}),
                "egress": False,
                "tool_timeout_s": 7.5,
                "secrets": json.dumps({}),
            },
        ),
        (
            "insert into agent_defaults (id, llm, asr, tts, vad, mcp, filler) values "
            "('singleton', :llm, :asr, :tts, :vad, :mcp, :filler)",
            {
                "llm": "claude",
                "asr": None,
                "tts": None,
                "vad": None,
                "mcp": json.dumps(["home"]),
                "filler": None,
            },
        ),
        (
            "insert into agents (name, prompt, llm, asr, tts, vad, mcp, filler) values "
            "(:name, :prompt, :llm, :asr, :tts, :vad, :mcp, :filler)",
            {
                "name": "sam",
                "prompt": "You are Sam.",
                "llm": None,
                "asr": None,
                "tts": None,
                "vad": None,
                "mcp": None,
                "filler": json.dumps({"enabled": True, "phrases": ["Hmm..."], "delay_ms": 1800.0}),
            },
        ),
        (
            "insert into devices (mac, agents) values (:mac, :agents)",
            {"mac": SEEDED_MAC, "agents": json.dumps(["sam"])},
        ),
        (
            "insert into domain_settings (key, value) values ('default_agent', :value)",
            {"value": json.dumps("sam")},
        ),
    ]
    with engine.begin() as connection:
        for statement, parameters in statements:
            connection.execute(text(statement), parameters)


def test_a_seeded_baseline_database_upgrades_to_head_with_every_value_kept(
    tmp_path: Path,
) -> None:
    from vinga_server.config.store import ConfigStore

    directory = tmp_path / "db"
    baseline = _at_baseline(directory)
    try:
        _seed_baseline_rows(baseline)
        assert _version(baseline) == ["0001"]
    finally:
        baseline.dispose()

    # What a server does on the first start after the upgrade.
    engine = open_database(directory)
    try:
        assert _version(engine) != ["0001"]
        domain = ConfigStore(engine).load().domain
    finally:
        engine.dispose()

    assert domain.providers.llm["claude"].type == "anthropic"
    assert domain.providers.llm["claude"].api_key_env == "ANTHROPIC_API_KEY"
    assert domain.providers.llm["claude"].options == {"model": "claude-sonnet-5"}
    entry = domain.mcp_servers["home"]
    assert (entry.command, entry.args) == ("uvx", ["home-mcp"])
    assert entry.env == {"API_ACCESS_TOKEN": "$HOME_TOKEN"}
    assert (entry.egress, entry.tool_timeout_s) == (False, 7.5)
    assert domain.agent_defaults.llm == "claude"
    assert domain.agent_defaults.mcp == ["home"]
    assert domain.agents["sam"].prompt == "You are Sam."
    assert domain.agents["sam"].filler is not None
    assert domain.agents["sam"].filler.phrases == ["Hmm..."]
    assert domain.devices == {SEEDED_MAC: ["sam"]}
    assert domain.default_agent == "sam"
    # And what 0002 added is unset on a row that predates it, which is
    # the whole of what a nullable additive column promises.
    assert entry.instructions is None
    # And what 0003 added is unset in the same way: the seeded rows
    # include no fragment, and no fragment exists to include.
    assert domain.prompt_fragments == {}
    assert domain.agent_defaults.prompt_includes is None
    assert domain.agents["sam"].prompt_includes is None
    # And what 0004 added: the opt-in reads false from the database
    # rather than from a Python-side rescue of NULL, which is what a
    # NOT NULL column with a database-level default is for, and the
    # nullable list beside it is unset.
    assert entry.use_server_instructions is False
    assert entry.inject_prompts is None
    # 0003 is additive in the other two shapes as well: a new table,
    # empty because nothing wrote a fragment, and a nullable column on
    # each layer table.
    engine = open_database(directory)
    try:
        assert EXPECTED_TABLES <= _tables(engine)
        for table, added in EXPECTED_COLUMNS.items():
            assert added <= _columns(engine, table)
        # The opt-in is false in the row rather than NULL in it, which
        # is the difference between a database that says the decision
        # and one whose readers each decide what NULL meant.
        with engine.connect() as connection:
            stored = connection.execute(
                text("select use_server_instructions from mcp_servers where name = 'home'")
            ).scalar()
        assert stored is not None
        assert not stored
    finally:
        engine.dispose()


def test_the_migrations_ship_inside_the_package() -> None:
    """Discovery from an installed wheel is proved in CI, which installs
    one and migrates from it. This is the cheap half: the scripts are
    inside the package directory hatchling builds, not beside it."""
    package = Path(vinga_server.__file__).resolve().parent
    migrations = package / "db" / "migrations"

    assert (migrations / "env.py").is_file()
    assert list((migrations / "versions").glob("*.py"))
