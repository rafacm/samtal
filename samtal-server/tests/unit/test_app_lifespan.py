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

import samtal_server.app as app_module
from samtal_server.app import StartupFailed, create_app, startup_failure
from samtal_server.config import Config
from samtal_server.conversations.store import DATABASE_FILENAME
from samtal_server.device.bindings import DeviceBindings
from samtal_server.providers import ProviderError
from samtal_server.providers import registry as provider_registry
from samtal_server.tools.mcp import McpServers
from tests.support.configs import config_with_agent

SENTENCE = "the llm provider 'mock' could not be built"


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


def opened_bindings(monkeypatch: pytest.MonkeyPatch) -> list[DeviceBindings]:
    """Every bindings view this run opens, in order."""
    opened: list[DeviceBindings] = []
    real = DeviceBindings.open.__func__  # type: ignore[attr-defined]

    def spy(cls: type[DeviceBindings], config: Config) -> DeviceBindings:
        view = real(cls, config)
        opened.append(view)
        return view

    monkeypatch.setattr(DeviceBindings, "open", classmethod(spy))
    return opened


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
    """Every provider this run constructs, as `stage.name`. Patched at
    the registry's own builder rather than at the boot entry point, so
    what it records is construction rather than a call."""
    built: list[str] = []
    real = provider_registry.build_provider

    def spy(stage: str, name: str, *args: Any, **kwargs: Any) -> object:
        built.append(f"{stage}.{name}")
        return real(stage, name, *args, **kwargs)

    monkeypatch.setattr(provider_registry, "build_provider", spy)
    return built


def refusing_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider build that refuses the way a misconfigured one does."""

    def refuse(*args: object, **kwargs: object) -> dict[str, Any]:
        raise ProviderError(SENTENCE)

    monkeypatch.setattr(app_module, "build_agent_providers", refuse)


def test_a_described_app_acquires_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole of acceptance criterion 6: build the app, never enter
    its lifespan, and nothing was opened, migrated, threaded or loaded."""
    opened = opened_bindings(monkeypatch)
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
        assert store._thread is not None and store._thread.is_alive()
        assert disposed == [], "the bindings pool went while the server was serving"

    assert disposed == [composition.bindings]
    assert stopped == ["mcp"]
    assert store._stopped
    assert not store._thread.is_alive()


def test_a_build_that_fails_part_way_releases_what_it_took(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The partial-startup case (the plan review's finding 6). The
    bindings pool is opened before the providers are built, so a provider
    failure has to unwind it: a boot that refused must not leave a
    connection pool behind on the way out."""
    opened = opened_bindings(monkeypatch)
    disposed = disposed_bindings(monkeypatch)
    refusing_providers(monkeypatch)

    app = create_app(recording_config(tmp_path))
    with pytest.raises(StartupFailed):
        with TestClient(app):
            pass

    assert len(opened) == 1, "the bindings pool was never opened, so this proves nothing"
    assert disposed == opened


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

    def explode(*args: object, **kwargs: object) -> dict[str, Any]:
        raise ZeroDivisionError("a bug, not a deployment problem")

    monkeypatch.setattr(app_module, "build_agent_providers", explode)

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
        assert mounted.loaded_agents == frozenset({"assistant"})
