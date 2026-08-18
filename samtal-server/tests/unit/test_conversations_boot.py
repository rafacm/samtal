"""What a boot does about the conversation store, and what it does not.

Three states, and the difference between them is the whole of acceptance
criterion 1. An enabled section opens and migrates `conversations.db` at
`create_app` and starts the writer in the lifespan. A disabled or absent
section starts nothing and creates nothing, and the proof that it also
*changes* nothing is the rest of the unit lane passing unmodified beside
this file. And a store that is already there is migrated either way,
because recording being off is not the same as what was recorded being
unreadable.

The store's own behaviour (the markers, the bound, retention, purge) has
its own suites next door. This one is about the wiring.
"""

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from samtal_server.app import create_app
from samtal_server.config import Config
from samtal_server.conversations import store as store_module
from samtal_server.conversations.store import (
    DATABASE_FILENAME,
    ConversationStore,
    conversations_path,
    open_conversations,
)
from tests.support.configs import config_with_agent

EXPECTED_TABLES = {"sessions", "turns", "tool_invocations", "events"}


def recording_config(tmp_path: Path, **conversations: object) -> Config:
    """A server whose databases live where this test can look at them."""
    section: dict[str, object] = {"enabled": True}
    section.update(conversations)
    return config_with_agent(
        server={"database": {"dir": str(tmp_path)}, "conversations": section}
    )


def quiet_config(tmp_path: Path, section: dict[str, object] | None = None) -> Config:
    """The same server with recording off: the section absent, or present
    and saying no."""
    server: dict[str, object] = {"database": {"dir": str(tmp_path)}}
    if section is not None:
        server["conversations"] = section
    return config_with_agent(server=server)


def head_revision(tmp_path: Path) -> str:
    """What a freshly migrated store is stamped with, read from one this
    test made rather than written down here: the revision moves with the
    chain, and a copy of it in a test would be a second place to update."""
    directory = tmp_path / "head"
    engine = open_conversations(directory)
    try:
        return _version(engine)
    finally:
        engine.dispose()


def _version(engine: Any) -> str:
    with engine.connect() as connection:
        (row,) = connection.execute(text("select version_num from alembic_version"))
    return str(row[0])


def _stamped(path: Path) -> tuple[str, set[str]]:
    engine = open_conversations(path.parent)
    try:
        return _version(engine), set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def events_named(caplog: pytest.LogCaptureFixture, name: str) -> list[Any]:
    return [r for r in caplog.records if getattr(r, "event", None) == name]


def test_an_enabled_boot_creates_and_migrates_the_store(tmp_path: Path) -> None:
    with TestClient(create_app(recording_config(tmp_path))):
        pass

    path = tmp_path / DATABASE_FILENAME
    assert path.is_file()
    version, tables = _stamped(path)
    assert version == head_revision(tmp_path)
    assert EXPECTED_TABLES <= tables


def test_an_enabled_boot_says_it_is_recording(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A warning, like capture's, because it means this server is keeping
    # what is said to it, and it names the file so an operator knows
    # which one to purge or to back up.
    with caplog.at_level("INFO"):
        with TestClient(create_app(recording_config(tmp_path))):
            pass

    (enabled,) = events_named(caplog, "conversations_enabled")
    assert enabled.levelname == "WARNING"
    assert enabled.path == str(tmp_path / DATABASE_FILENAME)


def test_the_file_is_open_before_the_lifespan_runs(tmp_path: Path) -> None:
    """Built cold at create_app: the file is opened and migrated where a
    boot failure is a boot failure, and only the thread waits for the
    lifespan. An app that is never entered therefore also leaks no
    thread, which is what makes `stop()` safe to call from anywhere."""
    app = create_app(recording_config(tmp_path))

    assert (tmp_path / DATABASE_FILENAME).is_file()
    store = app.state.composition.conversations
    assert store is not None
    assert store._thread is None
    store.stop()


@pytest.mark.parametrize(
    "section", [None, {"enabled": False}, {"enabled": False, "text": True}]
)
def test_recording_off_creates_nothing(
    tmp_path: Path, section: dict[str, object] | None, caplog: pytest.LogCaptureFixture
) -> None:
    # Criterion 1: an absent or disabled section leaves the server as it
    # was, which starts with leaving no database behind.
    with caplog.at_level("INFO"):
        with TestClient(create_app(quiet_config(tmp_path, section))) as client:
            assert client.app.state.composition.conversations is None

    assert not conversations_path(tmp_path).exists()
    # And says nothing about it. There is deliberately no disabled-mode
    # event: a new line for a section that is present and off would be
    # exactly the behaviour change the criterion forbids.
    assert not [
        record
        for record in caplog.records
        if str(getattr(record, "event", "")).startswith("conversations_")
    ]


def test_a_store_that_is_already_there_migrates_on_a_boot_that_records_nothing(
    tmp_path: Path,
) -> None:
    """An upgraded deployment that recorded last month and records
    nothing today still has to serve its history against the schema this
    server reads with. So a file that exists is brought to head even with
    the section off, which is maintenance of what exists rather than
    recording.

    The prior revision here is the one before the baseline: an empty
    file, with no version row at all, which is what any state behind head
    looks like to the upgrade."""
    path = conversations_path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(path).close()
    assert path.is_file()

    with TestClient(create_app(quiet_config(tmp_path, {"enabled": False}))):
        pass

    version, tables = _stamped(path)
    assert version == head_revision(tmp_path)
    assert EXPECTED_TABLES <= tables


def test_a_writer_that_cannot_start_leaves_stop_harmless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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

    store = ConversationStore(tmp_path)
    monkeypatch.setattr(store_module.threading, "Thread", Refusing)
    with pytest.raises(RuntimeError):
        store.start()

    assert store._thread is None
    # Harmless, and still idempotent.
    store.stop()
    store.stop()


def test_a_start_failure_in_the_lifespan_still_stops_the_store(tmp_path: Path) -> None:
    """The start is inside the lifespan's guarded region, so the writer
    that failed to start is stopped by the same `finally` that stops one
    that did."""
    app = create_app(recording_config(tmp_path))
    built = app.state.composition.conversations
    assert built is not None

    class Failing:
        stopped = False

        def start(self) -> None:
            raise RuntimeError("the writer would not start")

        def stop(self) -> None:
            Failing.stopped = True

    app.state.composition.conversations = Failing()
    try:
        with pytest.raises(RuntimeError):
            with TestClient(app):
                pass
    finally:
        built.stop()

    assert Failing.stopped, "a start that failed was never stopped"


def test_a_startup_failure_after_the_writer_started_still_stops_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The writer starts first inside the lifespan's guarded region, so
    anything that fails after it (filler synthesis, the MCP connects)
    still reaches the stop rather than leaving a thread behind a boot
    that never finished."""
    import samtal_server.app as app_module

    async def refuse(*args: object, **kwargs: object) -> dict[str, Any]:
        raise RuntimeError("the fillers would not synthesize")

    monkeypatch.setattr(app_module, "build_agent_fillers", refuse)
    app = create_app(recording_config(tmp_path))
    store = app.state.composition.conversations
    assert store is not None

    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass

    assert store._stopped
    assert store._thread is not None and not store._thread.is_alive()
