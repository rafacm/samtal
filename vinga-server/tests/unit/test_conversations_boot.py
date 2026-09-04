"""What a boot does about the conversation store, and what it does not.

Three states, and the difference between them is the whole of acceptance
criterion 1. An enabled section opens and migrates the `conversations`
schema in the lifespan and starts the writer there too. A disabled or
absent section starts no writer, and the proof that it also *changes*
nothing is the rest of the unit lane passing unmodified beside this
file. The schema is migrated in every one of the three, because
recording being off is not the same as what was recorded being
unreadable.

That last part is where the cutover moved the line (#283). What
"recording off creates nothing" used to mean was that no file was left
behind; there is no file, so what it means now is that no writer is
started and no row is written. Migrating a schema makes empty tables,
which is not a recording, and the tests below say so in exactly those
words.

The store's own behaviour (the markers, the bound, retention) has its
own suites next door. This one is about the wiring.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, text

import vinga_server.app as app_module
from tests.conftest import LANE_DATABASE
from tests.support.configs import config_with_agent
from vinga_server.app import StartupFailed, create_app
from vinga_server.config import Config
from vinga_server.config.loader import ConfigError, load_file_config
from vinga_server.config.models import (
    RESUMPTION_NEEDS_RECORDING,
    RESUMPTION_NEEDS_TEXT,
    ConversationsConfig,
    DatabaseConfig,
    FieldProblem,
    FieldProblemsError,
)
from vinga_server.conversations import schema
from vinga_server.conversations import store as store_module
from vinga_server.conversations.store import ConversationStore, open_conversations
from vinga_server.db import SUPERSEDED_REVISION, SUPERSEDED_REVISIONS, read_engine

EXPECTED_TABLES = {
    "sessions",
    "conversations",
    "turns",
    "tool_invocations",
    "conversation_milestones",
    "events",
}

SCHEMA = schema.SCHEMA


def recording_config(name: str | None = None, **conversations: object) -> Config:
    """A server recording into the database this lane provisioned, or
    into one the caller names."""
    section: dict[str, object] = {"enabled": True}
    section.update(conversations)
    database = {} if name is None else {"name": name}
    return config_with_agent(
        server={"database": database, "conversations": section}
    )


def quiet_config(
    name: str | None = None, section: dict[str, object] | None = None
) -> Config:
    """The same server with recording off: the section absent, or present
    and saying no."""
    server: dict[str, object] = {"database": {} if name is None else {"name": name}}
    if section is not None:
        server["conversations"] = section
    return config_with_agent(server=server)


def head_revision() -> str:
    """What a freshly migrated store is stamped with, read from one this
    test made rather than written down here: the revision moves with the
    chain, and a copy of it in a test would be a second place to update."""
    engine = open_conversations(DatabaseConfig())
    try:
        return _version(engine)
    finally:
        engine.dispose()


def _version(engine: Any) -> str:
    with engine.connect() as connection:
        (row,) = connection.execute(
            text(f"select version_num from {SCHEMA}.alembic_version")
        )
    return str(row[0])


def _stamped(name: str) -> tuple[str, set[str]]:
    engine = open_conversations(DatabaseConfig(name=name))
    try:
        return _version(engine), set(inspect(engine).get_table_names(schema=SCHEMA))
    finally:
        engine.dispose()


def _migrated(name: str) -> bool:
    """Whether the conversation schema exists in `name` at all, which is
    what "nothing was opened" comes to now that no file is left behind.

    Through the read engine, which creates nothing: an opener here would
    be the very act the test is asserting did not happen.
    """
    engine = read_engine(DatabaseConfig(name=name))
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text("select to_regnamespace(:schema) is not null"),
                    {"schema": SCHEMA},
                ).scalar()
            )
    finally:
        engine.dispose()


def events_named(caplog: pytest.LogCaptureFixture, name: str) -> list[Any]:
    return [r for r in caplog.records if getattr(r, "event", None) == name]


def test_an_enabled_boot_migrates_the_store(blank_database: str) -> None:
    with TestClient(create_app(recording_config(blank_database))):
        pass

    version, tables = _stamped(blank_database)
    assert version == head_revision()
    assert EXPECTED_TABLES <= tables


def test_an_enabled_boot_says_it_is_recording(caplog: pytest.LogCaptureFixture) -> None:
    # A warning, like capture's, because it means this server is keeping
    # what is said to it. It carries no value: what it used to name was
    # the file to back up, and a connection is not something an event may
    # carry (#283).
    with caplog.at_level("INFO"):
        with TestClient(create_app(recording_config())):
            pass

    (enabled,) = events_named(caplog, "conversations_enabled")
    assert enabled.levelname == "WARNING"
    assert not hasattr(enabled, "path")


def test_nothing_is_opened_before_the_lifespan_runs(blank_database: str) -> None:
    """The store is the lifespan's, schema and thread alike (#142): an
    app that is described and never served opens nothing, so a test lane
    or an import that builds one migrates nothing and leaves no thread
    running. The schema is created and migrated inside the lifespan,
    which is still boot, so a database the server cannot reach still
    fails the boot rather than the first conversation."""
    app = create_app(recording_config(blank_database))

    assert not _migrated(blank_database)

    with TestClient(app):
        store = app.state.composition.conversations
        assert store is not None
        assert _migrated(blank_database)
        # White-box for this file's thread reads: the store's writer
        # thread is what boot starts and shutdown joins, and whether one
        # is running is on no surface. Its absence is what the failures
        # below are about, and a test that walked away from a live one
        # would leave it for a neighbouring test to find.
        assert store._thread is not None and store._thread.is_alive()


@pytest.mark.parametrize(
    "section", [None, {"enabled": False}, {"enabled": False, "text": True}]
)
def test_recording_off_starts_no_writer_and_writes_no_rows(
    section: dict[str, object] | None, caplog: pytest.LogCaptureFixture
) -> None:
    """Criterion 1, restated for a store with no file (#283): an absent
    or disabled section leaves the server as it was, which now means no
    writer, no rows, and nothing said.

    One configuration, built once and used for both halves, because for
    a while this test was two halves naming two databases: the boot went
    through the payload door onto `vinga` while the count opened the
    lane's own, so the zero it asserted was about a database it had
    never touched and would have been zero however many rows the boot
    wrote (#333). The count is queried through THIS configuration's own
    section, and the connection is asked which database it landed in
    before it counts anything, so the two halves cannot come apart again
    without saying so.
    """
    config = quiet_config(section=section)
    assert config.server.database.name == LANE_DATABASE

    with caplog.at_level("INFO"):
        with TestClient(create_app(config)) as client:
            assert client.app.state.composition.conversations is None

    engine = open_conversations(config.server.database)
    try:
        with engine.connect() as connection:
            landed = connection.execute(text("select current_database()")).scalar()
            assert landed == LANE_DATABASE
            counted = connection.execute(
                text(f"select count(*) from {SCHEMA}.sessions")
            ).scalar()
    finally:
        engine.dispose()
    assert counted == 0
    # And says nothing about it. There is deliberately no disabled-mode
    # event: a new line for a section that is present and off would be
    # exactly the behaviour change the criterion forbids.
    assert not [
        record
        for record in caplog.records
        if str(getattr(record, "event", "")).startswith("conversations_")
    ]


def test_the_schema_is_migrated_on_a_boot_that_records_nothing(
    blank_database: str,
) -> None:
    """An upgraded deployment that recorded last month and records
    nothing today still has to serve its history against the schema this
    server reads with. So the chain is brought to head even with the
    section off, which is maintenance rather than recording: what it
    creates is empty tables.

    From a blank database, which is what a fresh deployment has and what
    any state behind head looks like to the upgrade."""
    with TestClient(create_app(quiet_config(blank_database, {"enabled": False}))):
        pass

    version, tables = _stamped(blank_database)
    assert version == head_revision()
    assert EXPECTED_TABLES <= tables


@pytest.mark.parametrize(
    ("section", "sentence", "pointer"),
    [
        (
            {"enabled": False, "resumption": True},
            RESUMPTION_NEEDS_RECORDING,
            "/enabled",
        ),
        (
            {"enabled": True, "text": False, "resumption": True},
            RESUMPTION_NEEDS_TEXT,
            "/text",
        ),
    ],
)
def test_resumption_without_what_it_reads_is_refused_at_boot(
    tmp_path: Path, section: dict[str, object], sentence: str, pointer: str
) -> None:
    """The two combinations in which resumption could only pretend, met
    where an operator meets them: at the boot's own read of the file.

    Both halves of the refusal are asserted because both are surface. The
    sentence is what a boot prints, and it names the two keys and the two
    ways out with no value in it; the pointer is what a form would take
    somebody to, and it is the switch to turn on rather than the one to
    turn off. The pointer is read off the model, which is the only place
    it survives: the loader renders a location and a sentence, and the
    field a validator knew about travels in the error's own context.
    """
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        yaml.safe_dump({"server": {"conversations": section}}), encoding="utf-8"
    )

    with pytest.raises(ConfigError) as refusal:
        load_file_config(config_file)
    assert sentence in str(refusal.value)

    with pytest.raises(ValidationError) as raised:
        ConversationsConfig(**section)
    (problem,) = _problems(raised.value)
    assert problem == FieldProblem(pointer, sentence)


def test_resumption_with_neither_switch_names_both(tmp_path: Path) -> None:
    """Two problems rather than the first one found: both are true, and
    an operator who fixed the one they were shown would meet the other on
    the next boot."""
    with pytest.raises(ValidationError) as raised:
        ConversationsConfig(enabled=False, text=False, resumption=True)

    assert [problem.path for problem in _problems(raised.value)] == ["/enabled", "/text"]


def _problems(exc: ValidationError) -> tuple[FieldProblem, ...]:
    """The field problems a validator raised, out of the error pydantic
    wrapped them in. `ctx` is where the exception object survives, which
    is what `_error_problems` reads in the renderers."""
    (error,) = exc.errors()
    raised = error["ctx"]["error"]
    assert isinstance(raised, FieldProblemsError)
    return raised.problems


def test_a_database_stamped_at_the_replaced_revision_is_refused(
    spare_database: str,
) -> None:
    """The thread schema arrived as a re-cut baseline with no migration
    and no backfill, so a database stamped at the revision it replaced
    cannot be upgraded and must not be half-read.

    Alembic cannot locate the deleted revision, and the classifier turns
    that into the one sentence that says what to do about it. Stamped
    directly rather than migrated from an old build, because this build
    ships no way to produce that state: the stamp IS the state.
    """
    engine = open_conversations(DatabaseConfig(name=spare_database))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(f"update {SCHEMA}.alembic_version set version_num = :old"),
                {"old": next(iter(SUPERSEDED_REVISIONS))},
            )
    finally:
        engine.dispose()

    with pytest.raises(StartupFailed) as refusal:
        with TestClient(create_app(recording_config(spare_database))):
            pass

    said = str(refusal.value)
    assert said == SUPERSEDED_REVISION
    # A fixed sentence: it names the procedure and the record, and no
    # value at all, the revision it found included.
    assert "deploy/postgres-init.sql" in said
    assert "database-upgrades-have-a-compatibility-floor" in said
    assert not any(revision in said for revision in SUPERSEDED_REVISIONS)


def test_a_revision_from_a_newer_build_is_not_told_to_reset(
    spare_database: str,
) -> None:
    """The other side of the closed set, and the reason it is closed.

    A database stamped by a later build and then met by an image that
    was rolled back fails Alembic in exactly the same way, and it is
    current rather than stranded: telling its operator to drop it would
    destroy a live volume over a rollback.
    """
    engine = open_conversations(DatabaseConfig(name=spare_database))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(f"update {SCHEMA}.alembic_version set version_num = :later"),
                {"later": "9999_from_the_future"},
            )
    finally:
        engine.dispose()

    with pytest.raises(StartupFailed) as refusal:
        with TestClient(create_app(recording_config(spare_database))):
            pass

    assert str(refusal.value) != SUPERSEDED_REVISION
    assert "9999_from_the_future" not in str(refusal.value)


def test_a_writer_that_cannot_start_leaves_stop_harmless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process out of threads is the case the lifespan's guard exists
    for, so what it calls next has to survive it: the thread is kept only
    once it is running, so `stop()` finds none to join and does what is
    left, which is letting go of the file."""

    class Refusing:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        def start(self) -> None:
            raise RuntimeError("no threads left")

    store = ConversationStore(DatabaseConfig())
    monkeypatch.setattr(store_module.threading, "Thread", Refusing)
    with pytest.raises(RuntimeError):
        store.start()

    # White-box, per the note in the first thread assertion above.
    assert store._thread is None
    # Harmless, and still idempotent.
    store.stop()
    store.stop()


def test_a_start_failure_in_the_lifespan_still_stops_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stop is registered on the lifespan's exit stack the moment the
    store is constructed and before it is started, so the writer that
    failed to start is released by the same unwinding that releases one
    that did.

    The store is replaced at its constructor rather than on the built
    composition, because the composition only exists once the build that
    would fail has already run (the plan review's finding 8)."""

    class Failing:
        stopped = False

        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        def start(self) -> None:
            raise RuntimeError("the writer would not start")

        def stop(self) -> None:
            Failing.stopped = True

    monkeypatch.setattr(app_module, "ConversationStore", Failing)

    with pytest.raises(RuntimeError):
        with TestClient(create_app(recording_config())):
            pass

    assert Failing.stopped, "a start that failed was never stopped"


def test_a_startup_failure_after_the_writer_started_still_stops_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The writer is started first among the things a startup starts, so
    anything that fails after it (filler synthesis, the MCP connects)
    still reaches the stop rather than leaving a thread behind a boot
    that never finished.

    The store this asserts on is caught at its constructor, which is the
    only way to hold a reference to one built inside a lifespan that
    then refused."""

    async def refuse(*args: object, **kwargs: object) -> dict[str, Any]:
        raise RuntimeError("the fillers would not synthesize")

    monkeypatch.setattr(app_module, "build_agent_fillers", refuse)

    built: list[ConversationStore] = []
    real = app_module.ConversationStore

    def recording(*args: object, **kwargs: object) -> ConversationStore:
        store = real(*args, **kwargs)
        built.append(store)
        return store

    monkeypatch.setattr(app_module, "ConversationStore", recording)

    with pytest.raises(RuntimeError):
        with TestClient(create_app(recording_config())):
            pass

    (store,) = built
    # White-box, per the note in the first thread assertion above.
    assert store._stopped
    assert store._thread is not None and not store._thread.is_alive()
