"""What an app that is never served holds, and what a served one lets go.

The acceptance criterion of #142 is a negative one: `create_app`
describes an application and acquires nothing, so an app that is built
and never entered has no engine, no thread, no model and no file. It
cannot be proved by reading the function, because the leak it replaced
was exactly that (a bindings engine opened at build and disposed in a
lifespan nothing entered), so it is proved here by sentinels around the
three acquisitions that cost something: the bindings pool, the
conversation store's file, and the providers.

The other two directions are the same claim from the other end: a
lifespan that is entered and left releases everything it took, and a
build that fails part way through releases what it had taken by then.
That last one is what the exit stack is for: every acquisition registers
its release as it is made, so there is no window in which a later
failure strands an earlier resource.

The startup-failure bridge is here too, because it is the same seam: a
boot failure inside the lifespan is caught, recorded as its sanitized
sentence, and re-raised as `StartupFailed` with nothing chained to it,
which is what keeps an operator's stderr to one line.
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.pool import Pool

import vinga_server.app as app_module
from tests.support.apps import entered_app
from tests.support.checkin import NORMALIZED, check_in, unbound_config
from tests.support.configs import config_with_agent
from tests.support.problems import problem
from vinga_server.app import StartupFailed, create_app, startup_failure
from vinga_server.composition import Composition
from vinga_server.config import Config
from vinga_server.config.api import UNEXPECTED, ApiRuntime
from vinga_server.config.entities import BINDING_NOTICE
from vinga_server.config.loader import DatabaseBusyError
from vinga_server.config.models import API_MOUNT_PATH
from vinga_server.conversations.store import DATABASE_FILENAME
from vinga_server.db import open_database
from vinga_server.device.bindings import DeviceBindings
from vinga_server.providers import ProviderError
from vinga_server.providers import world as provider_world
from vinga_server.providers.mock import MockTts
from vinga_server.tools.mcp import McpServers

SENTENCE = "the llm provider 'mock' could not be built"

API_SECRET_ENV = "VINGA_API_SECRET"

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

BEARER = {"Authorization": f"Bearer {TOKEN}"}


def recording_config(tmp_path: Path) -> Config:
    """A server whose every acquisition lands where this test can see it:
    the databases in a directory of its own, and recording on so the
    conversation store is one of them."""
    return config_with_agent(
        server={
            "database": {"dir": str(tmp_path)},
            "conversations": {"enabled": True},
        }
    )


def migrated(tmp_path: Path) -> None:
    """The configuration database, where a boot leaves it.

    Load bearing for every disposal assertion here, and for the
    check-in test at the end: `DeviceBindings.open` creates no database
    and opens no engine when there is no file, and answers from the boot
    snapshot instead, so a test that asserts a pool was let go without
    this has asserted `dispose()` on an object holding nothing. Both
    production entry points migrate this database before the app is
    built, through `load_boot_config`, which is what this stands in for.
    """
    open_database(tmp_path).dispose()


def engine_of(view: DeviceBindings) -> Engine:
    """The connection pool a bindings view is holding, and the check that
    it is holding one at all.

    `DeviceBindings.open` opens no engine when there is no database file,
    so without `migrated()` above a disposal assertion would be
    `dispose()` on an object holding nothing. This is what says so rather
    than letting such a test pass.
    """
    # White-box for this file's engine and thread reads. What a
    # lifespan promises is that what it took is given back: an engine
    # disposed, a pool released, a writer thread joined. A released
    # resource has no public form at all, which is the point of
    # releasing it, so ownership is asserted where it lives.
    engine = view._engine
    assert engine is not None, (
        "the bindings view opened no engine, so nothing below proves a disposal: "
        "the build has to open the configuration database before opening the view"
    )
    return engine


def opened_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[DeviceBindings], list[Pool]]:
    """Every bindings view this run opens, in order, and the connection
    pool each one was holding at the moment it was opened.

    The pool is captured here because a build that fails has already
    disposed by the time the test looks, and disposing an engine replaces
    its pool: holding the one that existed during the build is what lets
    a test tell a pool that was let go from one that was never there.
    """
    opened: list[DeviceBindings] = []
    pools: list[Pool] = []
    real = DeviceBindings.open.__func__  # type: ignore[attr-defined]

    def spy(cls: type[DeviceBindings], generations: Any) -> DeviceBindings:
        view = real(cls, generations)
        opened.append(view)
        # White-box, per the note in `engine_of` above.
        if view._engine is not None:
            pools.append(view._engine.pool)
        return view

    monkeypatch.setattr(DeviceBindings, "open", classmethod(spy))
    return opened, pools


def disposed_bindings(monkeypatch: pytest.MonkeyPatch) -> list[DeviceBindings]:
    """Every bindings view this run disposes, in order."""
    disposed: list[DeviceBindings] = []
    real = DeviceBindings.dispose

    def spy(self: DeviceBindings) -> None:
        disposed.append(self)
        real(self)

    monkeypatch.setattr(DeviceBindings, "dispose", spy)
    return disposed


def built_providers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every provider this run constructs, as `stage.name`.

    Patched where the construction is actually called from rather than
    at the boot entry point, so what it records is a provider coming
    into existence rather than somebody asking for one."""
    built: list[str] = []
    real = provider_world.construct_provider

    def spy(stage: str, name: str, *args: Any, **kwargs: Any) -> object:
        built.append(f"{stage}.{name}")
        return real(stage, name, *args, **kwargs)

    monkeypatch.setattr(provider_world, "construct_provider", spy)
    return built


def refusing_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider build that refuses the way a misconfigured one does."""

    async def refuse(*args: object, **kwargs: object) -> object:
        raise ProviderError(SENTENCE)

    monkeypatch.setattr(app_module, "build_world", refuse)


def test_a_described_app_acquires_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole of acceptance criterion 6: build the app, never enter
    its lifespan, and nothing was opened, migrated, threaded or loaded."""
    opened, _ = opened_bindings(monkeypatch)
    built = built_providers(monkeypatch)

    app = create_app(recording_config(tmp_path))

    assert opened == [], "the bindings pool was opened by an app nobody served"
    assert built == [], "a provider was constructed by an app nobody served"
    assert not (tmp_path / DATABASE_FILENAME).exists(), "the store was opened and migrated"
    # And the composition itself does not exist yet, which is the honest
    # signal for a reader that arrives too early: an attribute error
    # naming what has not been built, rather than a half-built object.
    assert not hasattr(app.state, "composition")


def test_entering_and_leaving_releases_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other end of the same claim: what the lifespan took, it gives
    back, in the reverse of the order it took it."""
    migrated(tmp_path)
    opened, pools = opened_bindings(monkeypatch)
    disposed = disposed_bindings(monkeypatch)
    stopped: list[str] = []
    real_stop_all = McpServers.stop_all

    async def spy_stop_all(self: McpServers) -> None:
        stopped.append("mcp")
        await real_stop_all(self)

    monkeypatch.setattr(McpServers, "stop_all", spy_stop_all)

    app = create_app(recording_config(tmp_path))
    with TestClient(app):
        composition = app.state.composition
        store = composition.conversations
        assert store is not None
        # White-box, per the note in `engine_of` above.
        assert store._thread is not None and store._thread.is_alive()
        assert disposed == [], "the bindings pool went while the server was serving"
        # Held open while it serves, and reading: a lookup is what the
        # OTA endpoint does on every check-in.
        engine = engine_of(composition.bindings)
        with engine.connect():
            pass
        assert engine.pool is pools[0]

    assert disposed == [composition.bindings]
    assert opened == [composition.bindings]
    # Disposing an engine replaces the pool it was holding, which is the
    # observable that separates a call to `dispose()` from the
    # connections actually being let go.
    assert engine.pool is not pools[0], "the connection pool outlived the server"
    assert stopped == ["mcp"]
    # White-box, per the note in `engine_of` above.
    assert store._stopped
    assert not store._thread.is_alive()


def test_the_engines_are_let_go_of_when_the_process_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last end a provider can meet, and the one an apply never
    reaches: the process stops.

    A retired world is released when nothing holds it and the world
    being served is released here, behind the drain, which is what makes
    the close a provider gained run at every end rather than at most of
    them.
    """
    closed: list[str] = []

    class Closing(MockTts):
        egress = False

        def __init__(self, **options: object) -> None:
            super().__init__(sample_rate=24000, ms_per_char=1.0, min_ms=20.0)

        async def close(self) -> None:
            closed.append("tts")

    monkeypatch.setattr(
        "vinga_server.providers.mock.build_tts", lambda label, config: Closing()
    )
    migrated(tmp_path)
    app = create_app(recording_config(tmp_path))

    with TestClient(app):
        assert closed == [], "the world being served let go of its voice"

    assert closed == ["tts"]


def test_a_boot_that_fails_after_the_engines_are_built_lets_go_of_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stretch between a build and the world it was built for.

    Constructing the conversation store, migrating it, starting its
    writer and synthesizing the filled pauses all happen after the
    engines exist and before any holder owns them, and every one of them
    can fail. A build whose objects nothing owned across that stretch
    would leak a loaded model per entry on exactly the boots an operator
    is already having a bad time on.
    """
    closed: list[str] = []

    class Closing(MockTts):
        egress = False

        def __init__(self, **options: object) -> None:
            super().__init__(sample_rate=24000, ms_per_char=1.0, min_ms=20.0)

        async def close(self) -> None:
            closed.append("tts")

    async def refuse(*args: object, **kwargs: object) -> object:
        raise ProviderError(SENTENCE)

    monkeypatch.setattr(
        "vinga_server.providers.mock.build_tts", lambda label, config: Closing()
    )
    # The next thing a boot does after the engines, and the first one
    # that can fail on a deployment rather than in a test.
    monkeypatch.setattr(app_module, "build_agent_fillers", refuse)
    migrated(tmp_path)

    app = create_app(recording_config(tmp_path))
    with pytest.raises(StartupFailed):
        with TestClient(app):
            pass

    assert closed == ["tts"], "the engines a failed boot had already built were leaked"


def test_a_build_that_fails_part_way_releases_what_it_took(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The partial-startup case (the plan review's finding 6). The
    bindings pool is opened part way through the build, so a step after
    it that refuses has to unwind it: a boot that refused must not leave
    a connection pool behind on the way out.

    The refusing step is the configuration store's own open, which is
    the first thing after the bindings view that a real deployment can
    fail at: another process holding the write lock is exactly that.
    The view is opened after the world it reads from is built, so a
    provider failure never reaches it and would prove nothing here.
    """
    migrated(tmp_path)
    opened, pools = opened_bindings(monkeypatch)
    disposed = disposed_bindings(monkeypatch)

    def refuse(directory: Path) -> Any:
        raise DatabaseBusyError("another process holds the write lock")

    monkeypatch.setattr(app_module, "open_store", refuse)

    app = create_app(recording_config(tmp_path))
    with pytest.raises(StartupFailed):
        with TestClient(app):
            pass

    assert len(opened) == 1, "the bindings pool was never opened, so this proves nothing"
    assert disposed == opened
    # And a pool that was really there and really let go, rather than a
    # `dispose()` on a view holding nothing: the engine the build
    # acquired is not holding the pool it acquired it with.
    engine = engine_of(opened[0])
    assert engine.pool is not pools[0], "a refused boot left its connection pool open"


def test_a_boot_failure_is_carried_out_as_one_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bridge (the plan review's finding 4). Uvicorn renders a
    lifespan exception as a traceback, so what it is handed carries the
    sanitized sentence and no chain at all: a provider exception's
    `__cause__` can hold what a client library was configured with."""
    refusing_providers(monkeypatch)

    app = create_app(recording_config(tmp_path))
    with pytest.raises(StartupFailed) as raised:
        with TestClient(app):
            pass

    assert str(raised.value) == SENTENCE
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    # And the sentence is where `main()` reads it, which is how the CLI
    # prints one line and exits 1 after `serve()` returns.
    assert startup_failure(app) == SENTENCE


def test_a_failure_outside_the_taxonomy_is_raised_as_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bug is not a boot failure. Only the refusals a deployment can
    cause are turned into a sentence; anything else keeps its type and
    its traceback, because somebody has to fix it."""

    async def explode(*args: object, **kwargs: object) -> object:
        raise ZeroDivisionError("a bug, not a deployment problem")

    monkeypatch.setattr(app_module, "build_world", explode)

    app = create_app(recording_config(tmp_path))
    with pytest.raises(ZeroDivisionError):
        with TestClient(app):
            pass

    assert startup_failure(app) is None


def test_a_server_that_came_up_says_so_and_one_that_did_not_stays_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`on_started` is the CLI's banner (the plan review's finding 11):
    it announces a server that is up, so a build that refused prints
    nothing."""
    said: list[str] = []

    with TestClient(create_app(recording_config(tmp_path), on_started=lambda: said.append("up"))):
        assert said == ["up"], "the banner was not said by a server that started"

    refusing_providers(monkeypatch)
    with pytest.raises(StartupFailed):
        with TestClient(
            create_app(recording_config(tmp_path), on_started=lambda: said.append("up again"))
        ):
            pass

    assert said == ["up"]


def test_the_api_gets_its_live_pieces_before_the_first_request(tmp_path: Path) -> None:
    """Starlette runs no lifespan for a mounted application, so the
    parent's is what installs the objects its requests resolve. Before
    the yield, and therefore before any request: the pending table the
    OTA endpoint writes is the one the claim route reads, and the agents
    it reports are the ones this server loaded."""
    app = create_app(recording_config(tmp_path))
    with TestClient(app):
        composition = app.state.composition
        mounted = composition.api

        assert mounted.pending is composition.pending
        assert mounted.mcp_servers is composition.mcp_servers
        assert mounted.loaded_agents() == frozenset({"assistant"})


# --- the database this build brings into existence --------------------
#
# A deployment whose configuration database is not there yet is an
# ordinary first boot, and the whole of what makes it interesting is
# that two things in this build look at that file: the engine the
# configuration API is served over and written through, and the bindings
# view every device path resolves through. What binds them is a promise
# the API makes in words: a device binding is the one write it says takes
# effect without a restart. The test below is that promise, driven end to
# end rather than read off the acknowledgement.


def _wrote(client: TestClient, path: str, body: object) -> None:
    response = client.put(f"{API_MOUNT_PATH}{path}", json=body, headers=BEARER)
    assert response.status_code == 200, response.text


def test_a_binding_written_through_the_api_is_live_at_the_next_check_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator binds a board through the API, and the board's next
    check-in resolves it: no restart, and nothing to do but ask again.

    Driven end to end because that is the only way it is worth pinning.
    The API writes to the configuration database and says the write is
    live; the OTA endpoint resolves through the bindings view, which is a
    second engine on the same file, opened at startup. Nothing in either
    half asserts the other, so an unclaimed board checks in and is sent
    round the ceremony, the binding is written, and the same board checks
    in again.

    `migrated` first, because that is the shape a server has: both
    production entry points compose their configuration out of this
    database, so the file is always there before the app is built. A
    build over a directory with no database is the test lane's shape and
    an embedded caller's, where the snapshot is the whole truth and the
    view says so authoritatively.

    The configuration deliberately names no default agent. With one, an
    unbound device resolves to it and the second check-in answers the
    same either way, which would make the whole end of this test agree
    with a bindings view that never read the database.
    """
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    migrated(tmp_path)

    with entered_app(unbound_config(database={"dir": str(tmp_path)})) as (app, client):
        # There is a database behind the view, so what follows is about
        # resolution and not about the fallback.
        composition: Composition = app.state.composition
        # White-box, per the note in `engine_of` above.
        assert composition.bindings._engine is not None

        # Nobody has claimed this board yet, which is what makes the
        # absence of a code below mean something.
        assert "activation" in check_in(client, mac=NORMALIZED)

        # A deployment configuring itself through its own API, in the
        # order the write-time reference checks require.
        for stage in ("llm", "asr", "tts", "vad"):
            _wrote(client, f"/providers/{stage}/mock", {"type": "mock"})
        _wrote(client, "/agent-defaults", dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"))
        _wrote(client, "/agents/assistant", {"prompt": "You are the assistant."})

        answer = client.put(
            f"{API_MOUNT_PATH}/devices/{NORMALIZED}",
            json={"agents": ["assistant"]},
            headers=BEARER,
        )
        assert answer.status_code == 200, answer.text
        # The acknowledgement claims the write is live rather than
        # waiting for a restart, which is the promise the check-in below
        # either keeps or breaks.
        assert answer.json()["notice"] == BINDING_NOTICE

        body = check_in(client, mac=NORMALIZED)

    assert "activation" not in body, "the bound device was sent round the ceremony again"
    assert body["websocket"]["token"]


def test_the_mounted_api_holds_the_engine_only_while_the_server_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine goes onto the mounted application's runtime when the
    lifespan opens it and comes off when the lifespan ends.

    Coming off is the half that has to be said out loud. `dispose()`
    replaces an engine's connection pool rather than closing the engine
    down, so an engine reached after disposal opens fresh connections
    quite happily: a handle left behind would let a request arriving
    after teardown open connections no lifespan owns and nothing will
    ever close. What such a request meets instead is the refusal for an
    application nobody is serving, sanitized on the way out like every
    other unexpected failure.
    """
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    app = create_app(recording_config(tmp_path))
    mounted = app.state.seed.api
    # Read off the mounted application rather than the composition,
    # because before the lifespan there is no composition to read, which
    # is the first of the three states under test. What is there is the
    # runtime `build_api` described, holding no engine because describing
    # an application acquires nothing.
    described: ApiRuntime = mounted.state.api_runtime
    assert described.store is None

    with TestClient(app) as client:
        runtime: ApiRuntime = mounted.state.api_runtime
        # The build installs the live one over it, and it is the same
        # object the composition carries.
        assert runtime is app.state.composition.api
        assert runtime.store is not None
        assert client.get(f"{API_MOUNT_PATH}/config", headers=BEARER).status_code == 200

    assert runtime.store is None
    assert mounted.state.api_runtime is runtime

    late = TestClient(app).get(f"{API_MOUNT_PATH}/config", headers=BEARER)

    assert late.status_code == 500
    assert late.json() == problem(500, UNEXPECTED)
