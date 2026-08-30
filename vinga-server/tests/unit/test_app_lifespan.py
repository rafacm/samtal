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

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.pool import Pool

import vinga_server.app as app_module
from tests.support.apps import entered_app
from tests.support.checkin import NORMALIZED, check_in, unbound_config
from tests.support.configs import config_with_agent
from tests.support.notices import CHECK_IN, boundaries
from tests.support.problems import refused
from vinga_server import __version__
from vinga_server.app import StartupFailed, create_app, startup_failure
from vinga_server.build_info import revision
from vinga_server.composition import Composition
from vinga_server.config import Config
from vinga_server.config.api import ApiRuntime
from vinga_server.config.loader import DatabaseBusyError
from vinga_server.config.models import API_MOUNT_PATH, DatabaseConfig
from vinga_server.conversations import ConversationStore
from vinga_server.db import DOMAIN_CHAIN, read_engine, write_engine
from vinga_server.device.bindings import DeviceBindings
from vinga_server.events import ServerEvents
from vinga_server.events.catalog import APP_CHANNEL, CaptureDisabled
from vinga_server.events.live import LiveEvents
from vinga_server.events.values import ConfiguredPath
from vinga_server.memory.store import MEMORY_CHAIN, MemoryStore
from vinga_server.onboarding.origin import onboarding_url
from vinga_server.providers import ProviderError
from vinga_server.providers import world as provider_world
from vinga_server.providers.mock import MockTts
from vinga_server.tools.mcp import McpServers

SENTENCE = "the llm provider 'mock' could not be built"

API_SECRET_ENV = "VINGA_API_SECRET"

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

BEARER = {"Authorization": f"Bearer {TOKEN}"}


def recording_config(name: str | None = None) -> Config:
    """A server whose every acquisition lands where this test can see it:
    recording on, so the conversation store is one of them, and the
    database this lane provisioned unless the caller names another."""
    database = {} if name is None else {"name": name}
    return config_with_agent(
        server={"database": database, "conversations": {"enabled": True}}
    )


def served(config: Config) -> FastAPI:
    """An app that stands for one a deployment runs: the configuration
    reads as if it came from the store, so the bindings view opens the
    engine whose disposal this file is about. A test whose subject is an
    app nobody served builds one with `create_app` directly."""
    return create_app(config, from_store=True)


def engine_of(view: DeviceBindings) -> Engine:
    """The connection pool a bindings view is holding, and the check that
    it is holding one at all."""
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


def memory_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[MemoryStore], list[MemoryStore]]:
    """Every memory store this run opens, and every one it closes.

    The boot's own two acts, asserted where nothing else asserts them
    (#314): a reply that reads memory proves the store it was handed,
    not the open that migrated the schema and not the close registered
    beside it, so an opener deleted from the build would leave a
    deployment migrating nothing and a suite that never noticed. What
    is pinned here is therefore the open, the close, and the schema
    really appearing in a database that had none.
    """
    opened: list[MemoryStore] = []
    closed: list[MemoryStore] = []
    real_open = app_module.open_memory
    real_close = MemoryStore.close

    def spy_open(settings: Any) -> MemoryStore:
        store = real_open(settings)
        opened.append(store)
        return store

    def spy_close(self: MemoryStore) -> None:
        closed.append(self)
        real_close(self)

    monkeypatch.setattr(app_module, "open_memory", spy_open)
    monkeypatch.setattr(MemoryStore, "close", spy_close)
    return opened, closed


def memory_head(name: str) -> list[str]:
    """What the memory chain in one database is stamped at, or nothing
    at all when the schema is not there."""
    engine = read_engine(DatabaseConfig(name=name))
    try:
        with engine.connect() as connection:
            found = connection.execute(
                text("select to_regnamespace(:name) is not null"),
                {"name": MEMORY_CHAIN.schema},
            ).scalar()
            if not found:
                return []
            return [
                row[0]
                for row in connection.execute(
                    text(f"select * from {MEMORY_CHAIN.schema}.alembic_version")
                )
            ]
    finally:
        engine.dispose()


def test_a_described_app_acquires_nothing(
    blank_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole of acceptance criterion 6: build the app, never enter
    its lifespan, and nothing was opened, migrated, threaded or loaded.

    Over a blank database, because "nothing was migrated" is the half
    that needs one: the lane's own database is migrated before the first
    test runs, so an empty schema list there would prove nothing.
    """
    opened, _ = opened_bindings(monkeypatch)
    built = built_providers(monkeypatch)

    app = create_app(recording_config(blank_database))

    assert opened == [], "the bindings pool was opened by an app nobody served"
    assert built == [], "a provider was constructed by an app nobody served"
    engine = read_engine(DatabaseConfig(name=blank_database))
    try:
        with engine.connect() as connection:
            found = connection.execute(
                text("select to_regnamespace(:name) is not null"),
                {"name": DOMAIN_CHAIN.schema},
            ).scalar()
    finally:
        engine.dispose()
    assert not found, "the schema was created by an app nobody served"
    # And the composition itself does not exist yet, which is the honest
    # signal for a reader that arrives too early: an attribute error
    # naming what has not been built, rather than a half-built object.
    assert not hasattr(app.state, "composition")


def test_entering_and_leaving_releases_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other end of the same claim: what the lifespan took, it gives
    back, in the reverse of the order it took it."""
    opened, pools = opened_bindings(monkeypatch)
    disposed = disposed_bindings(monkeypatch)
    stopped: list[str] = []
    real_stop_all = McpServers.stop_all

    async def spy_stop_all(self: McpServers) -> None:
        stopped.append("mcp")
        await real_stop_all(self)

    monkeypatch.setattr(McpServers, "stop_all", spy_stop_all)

    app = served(recording_config())
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
    monkeypatch: pytest.MonkeyPatch,
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
    app = create_app(recording_config())

    with TestClient(app):
        assert closed == [], "the world being served let go of its voice"

    assert closed == ["tts"]


def test_a_boot_that_fails_after_the_engines_are_built_lets_go_of_them(
    monkeypatch: pytest.MonkeyPatch,
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

    app = served(recording_config())
    with pytest.raises(StartupFailed):
        with TestClient(app):
            pass

    assert closed == ["tts"], "the engines a failed boot had already built were leaked"


def test_the_lifespan_migrates_the_memory_schema_and_lets_it_go(
    blank_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boot's whole relationship with the memory store, which is an
    open that migrates and a close registered beside it (#314).

    Over a blank database, because the migration is the half that needs
    one: the lane's own database is taken through every chain before the
    first test runs, so a head read there would be true whether this boot
    opened anything or not.

    Held open while the server serves and let go when it stops, in that
    order, because a store closed during the build would be one the
    cutover could not read a fact through.
    """
    opened, closed = memory_stores(monkeypatch)

    assert memory_head(blank_database) == [], (
        "the blank database already had the memory schema, so nothing below "
        "would prove the boot migrated it"
    )

    app = served(recording_config(blank_database))
    with TestClient(app):
        assert len(opened) == 1, "the boot opened no memory store"
        assert closed == [], "the memory store was let go while the server was serving"
        assert memory_head(blank_database) == ["2002_memory_scopes"]

    assert closed == opened, "the memory store outlived the server"


def test_a_boot_that_fails_after_the_memory_store_lets_go_of_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The partial-startup case for the store this milestone adds. It is
    opened part way through the build, so a step after it that refuses
    has to unwind it: two connection pools left behind on every refused
    boot is exactly what the exit stack exists to prevent."""
    opened, closed = memory_stores(monkeypatch)

    async def refuse(*args: object, **kwargs: object) -> object:
        raise ProviderError(SENTENCE)

    # A step the boot takes after the memory store is opened, and after
    # the conversation writer it is now opened in front of.
    monkeypatch.setattr(app_module, "build_agent_fillers", refuse)

    app = served(recording_config())
    with pytest.raises(StartupFailed):
        with TestClient(app):
            pass

    assert len(opened) == 1
    assert closed == opened, "a refused boot left the memory store's pools behind"


def teardown_order(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The order the two stores are let go in, which is the whole of what
    the composition reorder is for.

    Recorded rather than read off the source, because what decides it is
    the order the callbacks were registered in and that is exactly the
    kind of thing an edit reverses without meaning to.
    """
    order: list[str] = []
    closing = MemoryStore.close
    stopping = ConversationStore.stop

    def close(self: MemoryStore) -> None:
        order.append("memory")
        closing(self)

    def stop(self: ConversationStore) -> None:
        order.append("writer")
        stopping(self)

    monkeypatch.setattr(MemoryStore, "close", close)
    monkeypatch.setattr(ConversationStore, "stop", stop)
    return order


def test_the_writer_drains_before_the_memory_store_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The order a teardown unwinds in, and the reason memory is opened
    before the writer is constructed.

    The writer's drain runs retention, and retention deletes the memory
    of every thread it prunes in its own transaction: a drain that ran
    after memory had been let go would be a deletion made against a store
    the process had already closed. Registering the memory close first is
    what makes it unwind last.
    """
    order = teardown_order(monkeypatch)

    app = served(recording_config())
    with TestClient(app):
        assert order == [], "something was let go of while the server was serving"

    assert order == ["writer", "memory"]


def test_a_boot_that_fails_after_the_writer_starts_unwinds_in_the_same_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same order on the other exit. A partial startup unwinds
    through the same stack, so a refusal after the writer has started
    stops it and only then closes the store its retention writes
    through."""
    order = teardown_order(monkeypatch)

    async def refuse(*args: object, **kwargs: object) -> object:
        raise ProviderError(SENTENCE)

    monkeypatch.setattr(app_module, "build_agent_fillers", refuse)

    app = served(recording_config())
    with pytest.raises(StartupFailed):
        with TestClient(app):
            pass

    assert order == ["writer", "memory"]


def _wrote_state(conversation: str, key: str, value: str) -> None:
    """One conversation's note, written the way the store writes one.

    Through the store's own call rather than an insert, so what the
    sweep meets is a row this server produced; the aging below is the one
    thing no call can do, because what it stands for is time passing.
    """
    store = MemoryStore(
        write_engine(DatabaseConfig(), MEMORY_CHAIN), read_engine(DatabaseConfig())
    )
    try:
        asyncio.run(store.set_state(conversation, key, value, agent="poet"))
    finally:
        store.close()


def _aged(conversation: str, when: str) -> None:
    """Move one thread's ledger back in time, which is how a suite
    reaches a grace period without waiting out a day."""
    engine = write_engine(DatabaseConfig(), MEMORY_CHAIN)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "update memory.state set updated_at = :when "
                    "where conversation = :conversation"
                ),
                {"when": when, "conversation": conversation},
            )
    finally:
        engine.dispose()


def _state_of(conversation: str) -> list[tuple[str, str]]:
    engine = read_engine(DatabaseConfig())
    try:
        with engine.connect() as connection:
            return [
                (row[0], row[1])
                for row in connection.execute(
                    text(
                        "select key, value from memory.state "
                        "where conversation = :conversation"
                    ),
                    {"conversation": conversation},
                )
            ]
    finally:
        engine.dispose()


def test_the_boot_sweeps_the_memory_of_threads_nothing_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The heal for what no transaction covers, run where a server
    starts.

    An orphan older than the grace period is a pre-upgrade leftover, a
    thread that never landed a first turn, or a deployment that records
    nothing at all; a younger one is state that has simply arrived before
    its thread's first turn, which is the whole reason the grace period
    exists.
    """
    orphan = "1111111111111111aaaaaaaaaaaaaaaa"
    fresh = "2222222222222222bbbbbbbbbbbbbbbb"
    for conversation in (orphan, fresh):
        _wrote_state(conversation, "scene", "the tavern")
    _aged(orphan, "2020-01-01T00:00:00+00:00")

    # Named, so the server boots against the database these rows are in:
    # a configuration parsed from a dictionary carries the packaged
    # default rather than the one this lane provisioned.
    with TestClient(served(recording_config(DatabaseConfig().name))):
        pass

    assert _state_of(orphan) == []
    assert _state_of(fresh) == [("scene", "the tavern")]


def test_the_boot_sweep_happens_after_the_record_it_asks_about_exists(
    blank_database: str, caplog: pytest.LogCaptureFixture
) -> None:
    """What the sweep asks is which of these threads the record has
    never heard of, so the record's schema has to exist before it can be
    asked.

    Over a blank database, because that is the only place the ordering
    is observable: run in front of the migration, the anti-join meets a
    table that is not there, the containment turns that into
    `memory_cleanup_failed`, and the boot goes on having advertised a
    sweep it silently skipped. A boot that heals nothing says nothing.
    """
    with caplog.at_level("WARNING"):
        with TestClient(served(recording_config(blank_database))):
            pass

    assert [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "memory_cleanup_failed"
    ] == []


def test_a_build_that_fails_part_way_releases_what_it_took(
    monkeypatch: pytest.MonkeyPatch,
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
    opened, pools = opened_bindings(monkeypatch)
    disposed = disposed_bindings(monkeypatch)

    def refuse(database: Any) -> Any:
        raise DatabaseBusyError("another process holds the write lock")

    monkeypatch.setattr(app_module, "open_store", refuse)

    app = served(recording_config())
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bridge (the plan review's finding 4). Uvicorn renders a
    lifespan exception as a traceback, so what it is handed carries the
    sanitized sentence and no chain at all: a provider exception's
    `__cause__` can hold what a client library was configured with."""
    refusing_providers(monkeypatch)

    app = create_app(recording_config())
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bug is not a boot failure. Only the refusals a deployment can
    cause are turned into a sentence; anything else keeps its type and
    its traceback, because somebody has to fix it."""

    async def explode(*args: object, **kwargs: object) -> object:
        raise ZeroDivisionError("a bug, not a deployment problem")

    monkeypatch.setattr(app_module, "build_world", explode)

    app = create_app(recording_config())
    with pytest.raises(ZeroDivisionError):
        with TestClient(app):
            pass

    assert startup_failure(app) is None


def test_a_server_that_came_up_says_so_and_one_that_did_not_stays_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`on_started` is the CLI's banner (the plan review's finding 11):
    it announces a server that is up, so a build that refused prints
    nothing."""
    said: list[str] = []

    with TestClient(create_app(recording_config(), on_started=lambda: said.append("up"))):
        assert said == ["up"], "the banner was not said by a server that started"

    refusing_providers(monkeypatch)
    with pytest.raises(StartupFailed):
        with TestClient(
            create_app(recording_config(), on_started=lambda: said.append("up again"))
        ):
            pass

    assert said == ["up"]


def test_the_api_gets_its_live_pieces_before_the_first_request() -> None:
    """Starlette runs no lifespan for a mounted application, so the
    parent's is what installs the objects its requests resolve. Before
    the yield, and therefore before any request: the pending table the
    OTA endpoint writes is the one the claim route reads, and the agents
    it reports are the ones this server loaded."""
    app = served(recording_config())
    with TestClient(app):
        composition = app.state.composition
        mounted = composition.api

        assert mounted.pending is composition.pending
        assert mounted.mcp_servers is composition.mcp_servers
        assert mounted.loaded_agents() == frozenset({"assistant"})


def test_the_identity_the_api_answers_is_the_derivation_s_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one fact the configuration API answers that it must not
    derive: which deployment this is.

    `config/api.py` deliberately imports only the pending half of the
    onboarding package, and the import-weight pin holds it there, so the
    onboarding URL reaches the API the way every other runtime fact
    does: composed by the root and handed over. What that leaves open is
    the thing worth pinning, and only end to end: that what is handed
    over is the same value the derivation answers, rather than a second
    opinion assembled on the way.

    So this drives the served route and compares it against
    `onboarding.origin` and `build_info` called directly. Equality and
    not a shape check: a URL an operator types into a captive portal is
    wrong if it differs by one character from the one this server
    mounts, and the whole reason the derivation is in one place is that
    two of them would eventually differ by one character.
    """
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    config = config_with_agent(
        server={"public_url": "https://vinga.test.invalid", "onboarding": {"enabled": True}}
    )
    derived, origin = onboarding_url(config.server, "unused")
    app = served(config)
    with TestClient(app) as client:
        answered = client.get(f"{API_MOUNT_PATH}/runtime/info", headers=BEARER)

    assert answered.status_code == 200, answered.text
    assert answered.json() == {
        "version": __version__,
        "revision": revision(),
        "onboarding_enabled": True,
        "onboarding_url": derived,
        "onboarding_provenance": origin.provenance,
    }


def test_a_deployment_with_onboarding_off_answers_no_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the same composition: onboarding off is a state
    a deployment is legitimately in, and the read says so rather than
    refusing or inventing a path. The path devices are configured at is
    `server.ota_path`, which is this deployment's secret and is why
    nothing stands in the URL's place."""
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    config = config_with_agent(server={"onboarding": {"enabled": False}})
    app = served(config)
    with TestClient(app) as client:
        answered = client.get(f"{API_MOUNT_PATH}/runtime/info", headers=BEARER).json()

    assert answered["onboarding_enabled"] is False
    assert answered["onboarding_url"] is None
    assert answered["onboarding_provenance"] is None
    assert answered["revision"] == revision()
# --- the live event tap, which is process-global ----------------------
#
# The one acquisition in this build that is not a file, a socket or a
# thread, and the one whose leak would be invisible: the server tap set
# lives in the events package for the life of the process, so a hub
# attached by a lifespan and never detached would go on receiving every
# server event afterwards, and a second lifespan in the same process
# would deliver each of them to two hubs (#342). Both tests below emit
# one real server event and count the deliveries, which is the only
# place the difference shows.
#
# The lifespan is entered directly rather than through `TestClient`,
# because what is under test is the exit stack itself and a reader in
# this loop is what the deliveries have to reach.


def hubs_built(monkeypatch: pytest.MonkeyPatch) -> list[LiveEvents]:
    """Every hub a build constructs while this test runs, in order.

    A build that refuses installs no composition, so this is the only
    way to reach the hub it had already attached; the spy is the shape
    `opened_bindings` above already uses for the same reason.
    """
    built: list[LiveEvents] = []
    real = app_module.LiveEvents

    def spy(*args: Any, **kwargs: Any) -> LiveEvents:
        hub = real(*args, **kwargs)
        built.append(hub)
        return hub

    monkeypatch.setattr(app_module, "LiveEvents", spy)
    return built


async def delivered(subscription: Any) -> list[Any]:
    """Everything one reader was handed, without waiting for what it was
    not."""
    items: list[Any] = []
    while True:
        item = await subscription.next(timeout=0)
        if item is None:
            return items
        items.append(item)


def one_server_event(where: Path) -> None:
    """One real event on a server channel, which is what a detached hub
    must not hear."""
    ServerEvents(APP_CHANNEL).emit(
        lambda: CaptureDisabled(path=ConfiguredPath(str(where)))
    )


async def test_a_second_lifespan_does_not_deliver_the_events_twice(
    tmp_path: Path,
) -> None:
    """Two servers in one process, one after the other, which is what a
    test lane and an embedded caller both do. The first lifespan's hub
    is detached on its way out, so the second one's reader is the only
    one an event reaches."""
    first = served(recording_config())
    async with app_module.lifespan(first):
        retired = first.state.composition.live
    second = served(recording_config())
    async with app_module.lifespan(second):
        current = second.state.composition.live
        watching_retired = retired.subscribe()
        watching_current = current.subscribe()

        one_server_event(tmp_path)

        assert len(await delivered(watching_current)) == 1
        assert await delivered(watching_retired) == [], (
            "the retired lifespan's hub is still attached to the server tap"
        )


async def test_a_startup_that_failed_after_attaching_leaves_no_hub_behind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The hub is attached first, so that the boot's own events reach a
    tail; a boot that then refuses unwinds through the same exit stack
    everything else is registered on."""
    built = hubs_built(monkeypatch)
    refusing_providers(monkeypatch)
    app = served(recording_config())

    with pytest.raises(StartupFailed):
        async with app_module.lifespan(app):
            pass  # pragma: no cover - the startup refuses before this

    (hub,) = built
    watching = hub.subscribe()
    one_server_event(tmp_path)
    assert await delivered(watching) == []


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator binds a board through the API, and the board's next
    check-in resolves it: no restart, and nothing to do but ask again.

    Driven end to end because that is the only way it is worth pinning.
    The API writes to the configuration database and says the write is
    live; the OTA endpoint resolves through the bindings view, which is a
    second engine on the same database, opened at startup. Nothing in either
    half asserts the other, so an unclaimed board checks in and is sent
    round the ceremony, the binding is written, and the same board checks
    in again.

    The database the lane provisioned, which is the shape a server has:
    both production entry points compose their configuration out of it,
    so its schemas are always there before the app is built.

    The configuration deliberately names no default agent. With one, an
    unbound device resolves to it and the second check-in answers the
    same either way, which would make the whole end of this test agree
    with a bindings view that never read the database.
    """
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)

    with entered_app(unbound_config(), from_store=True) as (app, client):
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
        assert boundaries(answer.json()["notice"]) == {CHECK_IN}

        body = check_in(client, mac=NORMALIZED)

    assert "activation" not in body, "the bound device was sent round the ceremony again"
    assert body["websocket"]["token"]


def test_the_mounted_api_holds_the_engine_only_while_the_server_serves(
    monkeypatch: pytest.MonkeyPatch,
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
    app = create_app(recording_config())
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
    # The one refusal shape, saying nothing about what failed: an
    # application whose lifespan has been left is not a state a caller
    # can be told anything useful about.
    assert "log" in refused(late.json(), 500)
