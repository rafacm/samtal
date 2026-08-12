"""The live view of device bindings, at the edges a device reaches it.

Every test here boots one app on a real database and then writes to that
database through the repository, which is what an operator binding a
board does while the server runs. Nothing is rebuilt afterwards: the
whole claim of this milestone is that the same running app answers
differently, so a test that built a second app would be asserting the
boot-time snapshot instead.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from starlette.websockets import WebSocketDisconnect

from samtal_server.app import create_app
from samtal_server.config import Config, FileConfig, compose_config
from samtal_server.config.models import domain_fields
from samtal_server.config.store import ConfigStore
from samtal_server.db import open_database, read_engine
from samtal_server.device.bindings import DeviceBindings
from samtal_server.ota import OTA_PATH
from samtal_server.ws import WEBSOCKET_PATH

DEVICE_MAC = "aa:bb:cc:dd:ee:ff"
DEVICE_UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"

# A board this deployment already onboarded, which is what makes the
# database a bootable one while the device under test is unbound: boot
# refuses a configuration with agents that no device and no default
# agent reaches. Onboarding a second board is therefore the ordinary
# shape of these tests, not a contrivance.
BOUND_MAC = "11:22:33:44:55:01"

STAGES = ("llm", "asr", "tts", "vad")
AGENT = dict.fromkeys(STAGES, "mock")

DEVICE_HELLO = {
    "type": "hello",
    "version": 1,
    "features": {"mcp": True},
    "transport": "websocket",
    "audio_params": {
        "format": "opus",
        "sample_rate": 16000,
        "channels": 1,
        "frame_duration": 60,
    },
}


@contextlib.contextmanager
def store_at(directory: Path) -> Iterator[ConfigStore]:
    """The repository over the database in `directory`, the way the CLI
    and the API reach it: opened, used, disposed. This is the write side
    of every test here, and it is deliberately a different connection
    from the one the running app reads through."""
    engine = open_database(directory)
    try:
        yield ConfigStore(engine)
    finally:
        engine.dispose()


def booted(
    directory: Path,
    *,
    agents: tuple[str, ...] = ("assistant",),
    devices: dict[str, list[str]] | None = None,
    default_agent: str | None = None,
) -> Config:
    """The configuration a server booting on a database in `directory`
    would hold: the domain half written through the repository, read
    back, and composed onto a file half that names the same directory.

    The directory is what makes this different from the rest of the unit
    lane, where a `Config` is composed in memory and there is no database
    to write to afterwards.
    """
    if devices is None:
        devices = {BOUND_MAC: [agents[0]]}
    with store_at(directory) as store:
        for stage in STAGES:
            store.set_provider(stage, "mock", {"type": "mock"})
        for name in agents:
            store.set_agent(name, dict(AGENT))
        for mac, bound in devices.items():
            store.bind_device(mac, bound)
        if default_agent is not None:
            store.set_default_agent(default_agent)
        snapshot = store.load()
    return compose_config(
        FileConfig(server={"database": {"dir": str(directory)}}),
        domain_fields(snapshot.domain),
        "the test's database",
    )


def check_in(client: TestClient, device_id: str = DEVICE_MAC) -> dict:
    """One OTA check-in, the way the firmware makes it."""
    response = client.post(
        OTA_PATH,
        json={"application": {"version": "2.4.0"}, "board": {"type": "test-board"}},
        headers={"Device-Id": device_id, "Client-Id": DEVICE_UUID},
    )
    assert response.status_code == 200
    return response.json()


def token_of(client: TestClient, device_id: str = DEVICE_MAC) -> str:
    return check_in(client, device_id)["websocket"]["token"]


@contextlib.contextmanager
def connect(client: TestClient, token: str):
    headers = {
        "Authorization": f"Bearer {token}",
        "Protocol-Version": "1",
        "Device-Id": DEVICE_MAC,
        "Client-Id": DEVICE_UUID,
    }
    with client.websocket_connect(WEBSOCKET_PATH, headers=headers) as websocket:
        yield websocket


def hello(websocket) -> dict:
    websocket.send_text(json.dumps(DEVICE_HELLO))
    return json.loads(websocket.receive_text())


# What the two edges do with a binding written under them


def test_a_bind_is_seen_by_the_next_check_in_with_no_restart(tmp_path: Path) -> None:
    """The ceremony this exists for: an operator binds the board on the
    desk, and the board's own re-check hands it a token."""
    config = booted(tmp_path)
    with TestClient(create_app(config)) as client:
        assert token_of(client) == ""

        with store_at(tmp_path) as store:
            store.bind_device(DEVICE_MAC, ["assistant"])

        assert token_of(client) != ""


def test_a_bind_is_seen_by_the_next_connection_with_no_restart(tmp_path: Path) -> None:
    """The same app, the same socket path: what the device does three
    seconds later once the OTA reply has given it a token."""
    config = booted(tmp_path)
    with TestClient(create_app(config)) as client:
        # Signed the way the OTA reply would sign it, so this is the
        # handshake gate's real path and only the binding is in
        # question. Before the bind the socket is accepted and then
        # closed, because a device that has proved who it is deserves to
        # be told what is wrong.
        token = client.app.state.device_auth.issue(DEVICE_UUID, DEVICE_MAC)
        with connect(client, token) as websocket:
            with pytest.raises(WebSocketDisconnect) as refusal:
                websocket.receive_text()
        assert refusal.value.code == 1008

        with store_at(tmp_path) as store:
            store.bind_device(DEVICE_MAC, ["assistant"])

        with connect(client, token) as websocket:
            assert hello(websocket)["type"] == "hello"


def test_the_default_agent_is_live_too(tmp_path: Path) -> None:
    """Both inputs of the resolution are read, not just one: an unknown
    MAC follows `default_agent` as it stands now."""
    config = booted(tmp_path)
    with TestClient(create_app(config)) as client:
        assert token_of(client) == ""

        with store_at(tmp_path) as store:
            store.set_default_agent("assistant")

        assert token_of(client) != ""


def test_deleting_a_binding_stops_the_next_token(tmp_path: Path) -> None:
    """The allowlist is what it is now. A device removed while the
    server runs is refused at its next check-in rather than at the next
    restart."""
    config = booted(tmp_path, devices={DEVICE_MAC: ["assistant"]})
    with TestClient(create_app(config)) as client:
        assert token_of(client) != ""

        with store_at(tmp_path) as store:
            store.delete_device(DEVICE_MAC)

        assert token_of(client) == ""


def test_a_deleted_binding_does_not_reach_a_conversation_in_flight(
    tmp_path: Path,
) -> None:
    """Deliberate: a delete stops the next token and the next
    connection, not a conversation already happening."""
    config = booted(tmp_path, devices={DEVICE_MAC: ["assistant"]})
    with TestClient(create_app(config)) as client:
        token = token_of(client)
        with connect(client, token) as websocket:
            assert hello(websocket)["type"] == "hello"

            with store_at(tmp_path) as store:
                store.delete_device(DEVICE_MAC)

            # Still a conversation: the session resolved at connect and
            # nothing reaches back into it.
            websocket.send_text(json.dumps({"type": "listen", "state": "start"}))
            assert len(client.app.state.sessions) == 1

        # And the next connection is the one that is refused.
        assert token_of(client) == ""


# An agent this server never loaded


def test_a_binding_to_an_unloaded_agent_resolves_to_nothing(tmp_path: Path) -> None:
    """A binding written after boot can name an agent whose providers
    were never built. Handing that device a token would invite a
    websocket the session layer has to refuse."""
    config = booted(tmp_path)
    with TestClient(create_app(config)) as client:
        with store_at(tmp_path) as store:
            store.set_agent("poet", dict(AGENT))
            store.bind_device(DEVICE_MAC, ["poet"])

        assert token_of(client) == ""


def test_the_ota_line_names_the_restart_rather_than_the_binding(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The generic advice would send the operator to bind a device that
    is already bound. What is missing is this process."""
    config = booted(tmp_path)
    with TestClient(create_app(config)) as client:
        with store_at(tmp_path) as store:
            store.set_agent("poet", dict(AGENT))
            store.bind_device(DEVICE_MAC, ["poet"])

        with caplog.at_level(logging.WARNING):
            check_in(client)

    assert "bound to agent poet, which this server has not loaded" in caplog.text
    assert "restart to load it" in caplog.text
    assert "bind it under devices" not in caplog.text
    record = next(r for r in caplog.records if getattr(r, "event", None) == "ota_check")
    assert record.agents == []
    assert record.unloaded == ["poet"]


def test_the_session_line_names_the_restart_too(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A device that kept a token from before its agent was replaced
    reaches the websocket, and is told the same thing there."""
    config = booted(tmp_path, devices={DEVICE_MAC: ["assistant"]})
    with TestClient(create_app(config)) as client:
        token = token_of(client)
        with store_at(tmp_path) as store:
            store.set_agent("poet", dict(AGENT))
            store.bind_device(DEVICE_MAC, ["poet"])

        with caplog.at_level(logging.WARNING):
            with connect(client, token) as websocket:
                with pytest.raises(WebSocketDisconnect):
                    websocket.receive_text()

    assert "bound to agent poet, which this server has not loaded" in caplog.text
    rejection = next(
        r for r in caplog.records if getattr(r, "event", None) == "session_rejected"
    )
    assert rejection.reason == "agent_not_loaded"


def test_a_loaded_name_beside_an_unloaded_one_still_answers(tmp_path: Path) -> None:
    """The filter drops names rather than the whole binding, so a device
    bound to a loaded agent and a new one talks to the loaded one."""
    config = booted(tmp_path)
    with TestClient(create_app(config)) as client:
        with store_at(tmp_path) as store:
            store.set_agent("poet", dict(AGENT))
            store.bind_device(DEVICE_MAC, ["assistant", "poet"])

        assert token_of(client) != ""
        assert client.app.state.bindings.agents_for(DEVICE_MAC).agents == ("assistant",)


# When the database cannot be read


def test_an_unreadable_database_answers_from_the_boot_snapshot(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The OTA endpoint is every device's boot dependency, so a /data
    hiccup must not refuse the fleet's check-ins."""
    config = booted(tmp_path, devices={DEVICE_MAC: ["assistant"]})
    with TestClient(create_app(config)) as client:
        (tmp_path / "samtal.db").write_bytes(b"this is not a database")

        with caplog.at_level(logging.WARNING):
            assert token_of(client) != ""

    assert "cannot read the device bindings" in caplog.text
    # Staleness is in the log rather than in nobody's knowledge.
    assert any(
        getattr(record, "event", None) == "device_bindings_unreadable"
        for record in caplog.records
    )


def test_the_fallback_is_the_snapshot_and_not_an_empty_answer(tmp_path: Path) -> None:
    """"Fall back" means the configuration this server booted with, so a
    device the snapshot does not bind is still refused."""
    config = booted(tmp_path, devices={DEVICE_MAC: ["assistant"]})
    bindings = DeviceBindings.open(config)
    try:
        (tmp_path / "samtal.db").write_bytes(b"this is not a database")

        assert bindings.agents_for(DEVICE_MAC).agents == ("assistant",)
        assert bindings.agents_for("11:22:33:44:55:66").agents == ()
    finally:
        bindings.dispose()


def test_a_missing_database_is_the_snapshot_without_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A configuration composed in memory has no database to read, which
    is a state and not a failure: it is what most of this lane is."""
    config = Config(
        server={"database": {"dir": str(tmp_path / "nothing")}},
        providers={stage: {"mock": {"type": "mock"}} for stage in STAGES},
        agents={"assistant": AGENT},
        devices={DEVICE_MAC: ["assistant"]},
    )
    bindings = DeviceBindings.open(config)
    try:
        with caplog.at_level(logging.WARNING):
            assert bindings.agents_for(DEVICE_MAC).agents == ("assistant",)
    finally:
        bindings.dispose()

    assert caplog.records == []


def test_the_read_path_never_migrates(tmp_path: Path) -> None:
    """No Alembic on a device path, asserted rather than assumed: an
    empty file stays an empty file, where `open_database` would have
    built the whole schema in it."""
    (tmp_path / "samtal.db").touch()
    config = Config(server={"database": {"dir": str(tmp_path)}})
    bindings = DeviceBindings.open(config)
    try:
        assert bindings.agents_for(DEVICE_MAC).agents == ()
    finally:
        bindings.dispose()

    engine = read_engine(tmp_path)
    try:
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


# Contention


async def test_a_held_write_lock_stalls_neither_the_lookup_nor_the_loop(
    tmp_path: Path,
) -> None:
    """The property the read engine exists for. Every repository write
    holds the write lock for its whole transaction, and under WAL a
    deferred read takes no lock at all, so a device asking which agent
    it may talk to cannot queue behind an operator's write."""
    config = booted(tmp_path, devices={DEVICE_MAC: ["assistant"]})
    bindings = DeviceBindings.open(config)
    writer = open_database(tmp_path)
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0)
            ticks += 1

    ticker = asyncio.create_task(tick())
    try:
        with writer.connect() as held:
            # BEGIN IMMEDIATE fires on the first statement, so the write
            # lock is held from here until this block exits.
            held.execute(text("SELECT 1"))

            # Well inside the 10 second busy timeout a blocked read
            # would spend before failing.
            resolution = await asyncio.wait_for(bindings.resolve(DEVICE_MAC), timeout=2)
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
        writer.dispose()
        bindings.dispose()

    assert resolution.agents == ("assistant",)
    # The lookup ran on a worker thread, so the loop kept running other
    # coroutines while it was in the database.
    assert ticks > 0
