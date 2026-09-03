"""The live view of device bindings, at the edges a device reaches it.

Every test here boots one app on a real database and then writes to that
database through the repository, which is what an operator binding a
board does while the server runs. Nothing is rebuilt afterwards: the
whole claim of this milestone is that the same running app answers
differently, so a test that built a second app would be asserting the
boot-time snapshot instead.

`from_store=True` on every app here, and it is not ceremony: a server
whose domain half was read from a store resolves bindings live, and one
composed from a `Config` handed to it serves that snapshot
authoritatively (#283). `booted()` really does read the store, so these
apps say so; the two tests below whose subject IS the snapshot-only mode
compose their configuration in Python and say nothing, which is how they
get it.
"""

import asyncio
import contextlib
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text, update
from sqlalchemy.exc import OperationalError
from starlette.websockets import WebSocketDisconnect

from tests.conftest import TEST_API_SECRET
from tests.support.configs import BOUND_MAC, DEVICE_UUID, world
from tests.support.notices import CHECK_IN, RELOAD, boundaries
from tests.support.registry import AGENT, STAGES, booted, check_in, store_at
from tests.support.registry import BINDINGS_DEVICE_MAC as DEVICE_MAC
from vinga_server import logs
from vinga_server.app import create_app
from vinga_server.config import Config
from vinga_server.config.models import API_MOUNT_PATH, DatabaseConfig
from vinga_server.db import open_database, read_engine, schema
from vinga_server.device.bindings import DeviceBindings
from vinga_server.ws import WEBSOCKET_PATH

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It stands for whatever a database error carries
# that nobody wrote for a log line.
SENTINEL = "sk-test-4f8b2c9e-never-a-real-credential"

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
    config = booted()
    with TestClient(create_app(config, from_store=True)) as client:
        assert token_of(client) == ""

        with store_at() as store:
            store.bind_device(DEVICE_MAC, ["assistant"])

        assert token_of(client) != ""


def test_a_bind_is_seen_by_the_next_connection_with_no_restart(tmp_path: Path) -> None:
    """The same app, the same socket path: what the device does three
    seconds later once the OTA reply has given it a token."""
    config = booted()
    with TestClient(create_app(config, from_store=True)) as client:
        # Signed the way the OTA reply would sign it, so this is the
        # handshake gate's real path and only the binding is in
        # question. Before the bind the socket is accepted and then
        # closed, because a device that has proved who it is deserves to
        # be told what is wrong.
        token = client.app.state.composition.device_auth.issue(DEVICE_UUID, DEVICE_MAC)
        with connect(client, token) as websocket:
            with pytest.raises(WebSocketDisconnect) as refusal:
                websocket.receive_text()
        assert refusal.value.code == 1008

        with store_at() as store:
            store.bind_device(DEVICE_MAC, ["assistant"])

        with connect(client, token) as websocket:
            assert hello(websocket)["type"] == "hello"


def test_the_default_agent_is_live_too(tmp_path: Path) -> None:
    """Both inputs of the resolution are read, not just one: an unknown
    MAC follows `default_agent` as it stands now."""
    config = booted()
    with TestClient(create_app(config, from_store=True)) as client:
        assert token_of(client) == ""

        with store_at() as store:
            store.set_default_agent("assistant")

        assert token_of(client) != ""


def test_deleting_a_binding_stops_the_next_token(tmp_path: Path) -> None:
    """The allowlist is what it is now. A device removed while the
    server runs is refused at its next check-in rather than at the next
    restart."""
    config = booted(devices={DEVICE_MAC: ["assistant"]})
    with TestClient(create_app(config, from_store=True)) as client:
        assert token_of(client) != ""

        with store_at() as store:
            store.delete_device(DEVICE_MAC)

        assert token_of(client) == ""


def test_a_deleted_binding_does_not_reach_a_conversation_in_flight(
    tmp_path: Path,
) -> None:
    """Deliberate: a delete stops the next token and the next
    connection, not a conversation already happening."""
    config = booted(devices={DEVICE_MAC: ["assistant"]})
    with TestClient(create_app(config, from_store=True)) as client:
        token = token_of(client)
        with connect(client, token) as websocket:
            assert hello(websocket)["type"] == "hello"

            with store_at() as store:
                store.delete_device(DEVICE_MAC)

            # Still a conversation: the session resolved at connect and
            # nothing reaches back into it.
            websocket.send_text(json.dumps({"type": "listen", "state": "start"}))
            assert len(client.app.state.composition.sessions) == 1

        # And the next connection is the one that is refused.
        assert token_of(client) == ""


# An agent this server never loaded


def test_a_binding_to_an_unloaded_agent_resolves_to_nothing(tmp_path: Path) -> None:
    """A binding written after boot can name an agent whose providers
    were never built. Handing that device a token would invite a
    websocket the session layer has to refuse."""
    config = booted()
    with TestClient(create_app(config, from_store=True)) as client:
        with store_at() as store:
            store.set_agent("poet", dict(AGENT))
            store.bind_device(DEVICE_MAC, ["poet"])

        assert token_of(client) == ""


def test_the_ota_line_names_the_restart_rather_than_the_binding(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The generic advice would send the operator to bind a device that
    is already bound. What is missing is this process."""
    config = booted()
    with TestClient(create_app(config, from_store=True)) as client:
        with store_at() as store:
            store.set_agent("poet", dict(AGENT))
            store.bind_device(DEVICE_MAC, ["poet"])

        with caplog.at_level(logging.WARNING):
            check_in(client)

    assert "bound to agent poet, which this server is not serving" in caplog.text
    # The one action that puts the agent in front of the board, and no
    # restart: the world this server serves is one apply away from the
    # world the store describes.
    assert "vinga-server config apply" in caplog.text
    assert "restart" not in caplog.text
    assert "bind it under devices" not in caplog.text
    record = next(r for r in caplog.records if getattr(r, "event", None) == "ota_check")
    assert record.agents == []
    assert record.unloaded == ["poet"]


def test_the_session_line_names_the_reload_too(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A device that kept a token from before its agent was replaced
    reaches the websocket, and is told the same thing there.

    The same thing including the action: two records about one state
    that named two different remedies would be worse than either, since
    an operator reads whichever one their query happened to reach.
    """
    config = booted(devices={DEVICE_MAC: ["assistant"]})
    with TestClient(create_app(config, from_store=True)) as client:
        token = token_of(client)
        with store_at() as store:
            store.set_agent("poet", dict(AGENT))
            store.bind_device(DEVICE_MAC, ["poet"])

        with caplog.at_level(logging.WARNING):
            with connect(client, token) as websocket:
                with pytest.raises(WebSocketDisconnect):
                    websocket.receive_text()

    assert "bound to agent poet, which this server is not serving" in caplog.text
    assert "vinga-server config apply" in caplog.text
    assert "restart" not in caplog.text
    rejection = next(
        r for r in caplog.records if getattr(r, "event", None) == "session_rejected"
    )
    # The token is unchanged and deliberately so: it is the closed-set
    # value a collector groups by, not a sentence, and moving it would
    # break every query written against it to say the same thing the
    # sentence beside it already says.
    assert rejection.reason == "agent_not_loaded"


def test_a_loaded_name_beside_an_unloaded_one_still_answers(tmp_path: Path) -> None:
    """The split drops names rather than the whole binding, so a device
    bound to a served agent and one this server is not serving talks to
    the one it can.

    The view answers both names, because which of them can be served is
    not its question; the split against the world being served is what
    separates them, and it is the same call the check-in above made.
    """
    config = booted()
    with TestClient(create_app(config, from_store=True)) as client:
        with store_at() as store:
            store.set_agent("poet", dict(AGENT))
            store.bind_device(DEVICE_MAC, ["assistant", "poet"])

        assert token_of(client) != ""
        composition = client.app.state.composition
        bound = composition.bindings.names_for(DEVICE_MAC)
        assert bound.names == ("assistant", "poet")

        served = bound.against(composition.generations.current().config.agents)
        assert (served.agents, served.unloaded) == (("assistant",), ("poet",))


# The agent at the other end of the binding
#
# Two things have to be true for a device to be served, and only one of
# them was ever live: the binding, which this view reads on every
# lookup, and the agent it names, which a server has to be serving. That
# second half moves at a reload now, so the loop below is the whole of
# what an operator does to put a new agent in front of a board, and none
# of it involves stopping the server.


RELOAD_PATH = f"{API_MOUNT_PATH}/runtime/config/reload"

BEARER = {"Authorization": f"Bearer {TEST_API_SECRET}"}


def test_an_agent_added_by_an_apply_is_served_at_the_next_check_in(tmp_path: Path) -> None:
    """Write the agent, bind the board, ask the server to apply, and the
    board's own next check-in hands it a token.

    Every step goes through the surfaces an operator actually uses, and
    the acknowledgements are asserted along the way, because what stands
    between the operator and the board here is knowing which of them is
    still pending: before the apply the binding is live and the agent is
    not, and the sentence says exactly that and which command fixes it.
    """
    config = booted()
    with TestClient(create_app(config, from_store=True)) as client:
        assert (
            client.put(
                f"{API_MOUNT_PATH}/agents/poet", json=dict(AGENT), headers=BEARER
            ).status_code
            == 200
        )
        bound = client.put(
            f"{API_MOUNT_PATH}/devices/{DEVICE_MAC}",
            json={"agents": ["poet"]},
            headers=BEARER,
        )
        assert bound.status_code == 200
        assert boundaries(bound.json()["notice"]) == {CHECK_IN, RELOAD}
        # The binding is live and the agent is not, so the board is
        # still turned away.
        assert token_of(client) == ""

        applied = client.post(RELOAD_PATH, headers=BEARER)

        assert applied.status_code == 200, applied.text
        assert applied.json()["agents"]["added"] == ["poet"]
        # And the board is served, with no restart between the write and
        # this line.
        assert token_of(client) != ""
        # The same write acknowledged again says the live sentence now,
        # which is the operator-visible half of the same fact.
        again = client.put(
            f"{API_MOUNT_PATH}/devices/{DEVICE_MAC}",
            json={"agents": ["poet"]},
            headers=BEARER,
        )
        assert boundaries(again.json()["notice"]) == {CHECK_IN}


def test_an_agent_removed_by_an_apply_is_out_of_reach_at_the_next_one(
    tmp_path: Path,
) -> None:
    """The other direction, at the same edge. The store drops the agent
    and the binding that named it, the apply installs that world, and
    the board that was being served is turned away at its next check-in.

    What a conversation already open does is not this edge's question
    and is pinned where a conversation is: it finishes on the world it
    was built from.
    """
    config = booted(devices={DEVICE_MAC: ["assistant"]})
    with TestClient(create_app(config, from_store=True)) as client:
        assert token_of(client) != ""

        with store_at() as store:
            store.delete_device(DEVICE_MAC)
            store.delete_agent("assistant")

        applied = client.post(RELOAD_PATH, headers=BEARER)

        assert applied.status_code == 200, applied.text
        assert applied.json()["agents"]["removed"] == ["assistant"]
        assert token_of(client) == ""


# When the database cannot be read


def test_an_unreadable_database_answers_from_the_boot_snapshot(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The OTA endpoint is every device's boot dependency, so a database
    hiccup must not refuse the fleet's check-ins.

    The engine is replaced rather than the file corrupted, which is what
    a database going away looks like from this side: there is no file to
    overwrite with rubbish (#283), and what the view meets either way is
    a read it cannot make."""
    config = booted(devices={DEVICE_MAC: ["assistant"]})
    with TestClient(create_app(config, from_store=True)) as client:
        view = client.app.state.composition.bindings
        view._engine.dispose()
        monkeypatch.setattr(view, "_engine", _FailingEngine())

        with caplog.at_level(logging.WARNING):
            assert token_of(client) != ""

    assert "cannot read the device bindings" in caplog.text
    # Staleness is in the log rather than in nobody's knowledge.
    assert any(
        getattr(record, "event", None) == "device_bindings_unreadable"
        for record in caplog.records
    )


def _rendered(records: list[logging.LogRecord]) -> tuple[str, list[dict]]:
    """The captured records in both shipped formats: the human one, and
    the JSON one a container writes. A record is only safe when it is
    safe in both, the discipline the M1 review settled on."""
    text = logging.Formatter(logs.TEXT_FORMAT)
    formatter = logs.JsonFormatter()
    return "\n".join(text.format(record) for record in records), [
        json.loads(formatter.format(record)) for record in records
    ]


class _FailingEngine:
    """An engine whose every connection fails, carrying what a real one
    can carry.

    A DBAPI error is not a sentence somebody wrote for a log: SQLAlchemy
    attaches the statement and the parameters bound to it, and a driver
    message quotes the path or the value it choked on. This one puts the
    sentinel in all three places a naive log line would have reached
    for, which is what makes the assertion below about the rule rather
    than about any one driver's fixed wording.
    """

    def connect(self) -> object:
        raise OperationalError(
            f"SELECT agents FROM devices WHERE mac = ? -- {SENTINEL}",
            {"mac": SENTINEL},
            RuntimeError(f"disk I/O error near {SENTINEL}"),
        )

    def dispose(self) -> None:
        return None


def test_a_failed_read_repeats_nothing_the_failure_carried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The warning is written on a path anything in the stored
    configuration can reach, so it is a fixed sentence plus the
    exception's class name, and nothing else."""
    config = Config(
        providers={stage: {"mock": {"type": "mock"}} for stage in STAGES},
        agents={"assistant": AGENT},
        devices={DEVICE_MAC: ["assistant"]},
    )
    bindings = DeviceBindings(world(config), _FailingEngine())

    with caplog.at_level(logging.WARNING):
        # The snapshot answered, which is the other half of the rule.
        assert bindings.names_for(DEVICE_MAC).names == ("assistant",)

    text, objects = _rendered(caplog.records)
    assert objects, "the fallback went unlogged"
    assert SENTINEL not in text
    assert all(SENTINEL not in json.dumps(payload) for payload in objects)
    # What is said instead: the fallback, and the kind of failure as the
    # repository classifies it.
    assert objects[0]["event"] == "device_bindings_unreadable"
    assert objects[0]["failure"] == "StorageError"
    assert objects[0]["device"] == DEVICE_MAC
    # And nothing forged: one object per record, each a single line.
    assert len(text.splitlines()) == len(caplog.records)


def _write_agents_column(mac: str, value: object) -> None:
    """A row put beyond what any write could have produced, which is the
    state a live reader has to have an answer for."""
    engine = open_database(DatabaseConfig())
    try:
        with engine.begin() as connection:
            connection.execute(
                update(schema.devices).where(schema.devices.c.mac == mac).values(agents=value)
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "stored",
    [
        # A string, the dangerous one: iterating it succeeds and yields
        # its characters, so a reader that skipped the array check would
        # bind this device to eleven one-character agents.
        "assistant",
        # An empty binding, which the write path refuses and which would
        # otherwise read as "this device is bound to nothing".
        [],
        # A blank name, and a name repeated, both refused everywhere else.
        ["   "],
        ["assistant", "assistant"],
        # Not names at all.
        [17],
        {"agents": ["assistant"]},
    ],
)
def test_a_row_no_write_could_have_made_falls_back_rather_than_refusing(
    stored: object, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The rules that govern a binding are the repository's, and the live
    reader runs them. A row that breaks one is unreadable, not empty:
    reading it as "bound to nothing" would turn a device away over a
    fact nobody established."""
    config = booted(devices={DEVICE_MAC: ["assistant"]})
    bindings = DeviceBindings.open(world(config))
    try:
        _write_agents_column(DEVICE_MAC, stored)

        with caplog.at_level(logging.WARNING):
            resolved = bindings.names_for(DEVICE_MAC)
    finally:
        bindings.dispose()

    assert resolved.names == ("assistant",)
    assert any(
        getattr(record, "event", None) == "device_bindings_unreadable"
        for record in caplog.records
    )


def test_a_default_agent_that_is_not_a_name_falls_back_too(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The other row, and the one that used to be read as None: a
    malformed default agent would have quietly turned every unbound
    device away."""
    config = booted(devices={BOUND_MAC: ["assistant"]}, default_agent="assistant")
    bindings = DeviceBindings.open(world(config))
    try:
        engine = open_database(DatabaseConfig())
        try:
            with engine.begin() as connection:
                connection.execute(
                    update(schema.domain_settings)
                    .where(schema.domain_settings.c.key == schema.DEFAULT_AGENT_KEY)
                    .values(value=17)
                )
        finally:
            engine.dispose()

        with caplog.at_level(logging.WARNING):
            resolved = bindings.names_for(DEVICE_MAC)
    finally:
        bindings.dispose()

    # The snapshot's default agent, rather than silence.
    assert resolved.names == ("assistant",)
    assert any(
        getattr(record, "event", None) == "device_bindings_unreadable"
        for record in caplog.records
    )


def test_the_fallback_is_the_snapshot_and_not_an_empty_answer() -> None:
    """"Fall back" means the configuration this server booted with, so a
    device the snapshot does not bind is still refused."""
    config = booted(devices={DEVICE_MAC: ["assistant"]})
    bindings = DeviceBindings.open(world(config))
    try:
        bindings._engine.dispose()
        bindings._engine = _FailingEngine()  # type: ignore[assignment]

        assert bindings.names_for(DEVICE_MAC).names == ("assistant",)
        assert bindings.names_for("11:22:33:44:55:66").names == ()
    finally:
        bindings.dispose()


def test_a_composed_configuration_is_the_snapshot_without_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A configuration composed in memory is a world no store describes,
    which is a state and not a failure: it is what most of this lane is.

    It used to be decided by a database file not being there, and the
    probe went with the file (#283). What decides now is how the server
    was composed, which is why this builds the view the way a server
    composed from a `Config` builds it.
    """
    config = Config(
        providers={stage: {"mock": {"type": "mock"}} for stage in STAGES},
        agents={"assistant": AGENT},
        devices={DEVICE_MAC: ["assistant"]},
    )
    bindings = DeviceBindings.snapshot_only(world(config))
    try:
        with caplog.at_level(logging.WARNING):
            assert bindings.names_for(DEVICE_MAC).names == ("assistant",)
        assert bindings.snapshot_authoritative
    finally:
        bindings.dispose()

    assert caplog.records == []


def test_a_database_that_goes_away_is_a_loud_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The engine connects lazily, so the database can go between the
    app being built and the first device asking: an instance restarts, a
    network partitions, a restore is in progress. What must not happen
    then is a device answered "bound to nothing" for as long as nobody
    noticed."""
    config = booted(devices={DEVICE_MAC: ["assistant"]})
    bindings = DeviceBindings.open(world(config))
    try:
        bindings._engine.dispose()
        bindings._engine = _FailingEngine()  # type: ignore[assignment]

        with caplog.at_level(logging.WARNING):
            resolved = bindings.names_for(DEVICE_MAC)
    finally:
        bindings.dispose()

    assert resolved.names == ("assistant",)
    assert any(
        getattr(record, "event", None) == "device_bindings_unreadable"
        for record in caplog.records
    )


def test_the_read_path_never_migrates(blank_database: str) -> None:
    """No Alembic on a device path, asserted rather than assumed: a
    blank database stays blank, where `open_database` would have created
    the schema and built the whole shape in it."""
    settings = DatabaseConfig(name=blank_database)
    engine = read_engine(settings)
    try:
        with engine.connect() as connection:
            assert not connection.execute(
                text("select to_regnamespace('domain') is not null")
            ).scalar()
    finally:
        engine.dispose()


# Contention


async def test_a_held_write_lock_stalls_neither_the_lookup_nor_the_loop(
    tmp_path: Path,
) -> None:
    """The property the read engine exists for. Every repository write
    holds the chain's advisory lock for its whole transaction, and a
    reader takes no advisory lock at all, so a device asking which agent
    it may talk to cannot queue behind an operator's write.

    The counter is read on both sides of the lookup rather than at the
    end, so what is asserted is that the loop ran other coroutines
    during it, and the writer is asked whether it is still in its
    transaction after the lookup returned, so the interval the lookup
    ran in is the interval the lock was held. That a whole conversation
    also stays live under the same lock is the integration lane's, where
    there is a device to have one with."""
    config = booted(devices={DEVICE_MAC: ["assistant"]})
    bindings = DeviceBindings.open(world(config))
    writer = open_database(DatabaseConfig())
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0)
            ticks += 1

    ticker = asyncio.create_task(tick())
    try:
        with writer.connect() as held:
            # The begin listener takes the advisory lock on the first
            # statement, so it is held from here until this block exits.
            held.execute(text("SELECT 1"))
            assert held.in_transaction()
            before = ticks

            # Well inside the 10 second lock timeout a blocked read
            # would spend before failing.
            resolution = await asyncio.wait_for(bindings.resolve(DEVICE_MAC), timeout=2)

            # Both facts about the same interval: the loop kept going
            # while the lookup was in the database, and the lock was
            # still held when the lookup came back with its answer.
            during = ticks - before
            still_held = held.in_transaction()
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
        writer.dispose()
        bindings.dispose()

    assert resolution.names == ("assistant",)
    assert during > 0
    assert still_held


# What a stale answer may and may not be used for


def test_a_failed_read_answers_but_does_not_call_a_device_unbound(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback keeps a fleet's check-ins served, and that is all it
    may do. An empty answer out of it is not the database saying nothing
    is bound; it is this server not having been able to find out, and
    the activation ceremony reads exactly that emptiness as an invitation
    to mint a claim ticket."""
    config = booted(devices={BOUND_MAC: ["assistant"]})
    with TestClient(create_app(config, from_store=True)) as client:
        # Bound after boot, so the snapshot this server holds knows
        # nothing about it: the database is the only place it exists.
        with store_at() as store:
            store.bind_device(DEVICE_MAC, ["assistant"])
        view = client.app.state.composition.bindings
        view._engine.dispose()
        monkeypatch.setattr(view, "_engine", _FailingEngine())

        with caplog.at_level(logging.WARNING):
            body = check_in(client)

    # No token, since the snapshot has no binding for it, and no code
    # either, since the snapshot is not what decides that.
    assert body["websocket"]["token"] == ""
    assert "activation" not in body
    assert "no activation code was issued" in caplog.text
    assert any(
        record.__dict__.get("reason") == "unreadable" for record in caplog.records
    )


def test_a_stale_answer_still_carries_the_snapshots_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half: what the fallback is for. A device bound at boot
    keeps being served through a database hiccup, which is why the
    fallback exists at all."""
    config = booted(devices={DEVICE_MAC: ["assistant"]})
    with TestClient(create_app(config, from_store=True)) as client:
        view = client.app.state.composition.bindings
        view._engine.dispose()
        monkeypatch.setattr(view, "_engine", _FailingEngine())

        body = check_in(client)

    assert body["websocket"]["token"] != ""
    assert "activation" not in body


def test_a_view_with_no_database_answers_authoritatively() -> None:
    """A configuration composed in memory has no database to be stale
    against: the snapshot is the whole truth there is, which is what the
    unit lane and an embedded server have, and a code is minted there."""
    snapshot_only = DeviceBindings.snapshot_only(world(Config()))

    assert snapshot_only.names_for(DEVICE_MAC).authoritative is True
