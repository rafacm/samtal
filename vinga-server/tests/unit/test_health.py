"""The two probes, and what each of them is entitled to know.

`/healthz` says this process is alive and serving its control surface.
`/readyz` says it may be handed a new device conversation, which is the
other question and diverges from the first exactly when it matters: a
draining server is alive and refusing, and a full one is alive and has
no slot.

What is here is what needs no lifespan: an application that was
described and never served, and a serving one whose composition is put
on its state directly. The composed app, the drain transition and the
whole lifecycle are the integration lane's, where there is a database to
build one against.
"""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from tests.support.events import both_formats
from vinga_server import __version__
from vinga_server.app import create_app
from vinga_server.build_info import REVISION_ENV, revision
from vinga_server.config import Config
from vinga_server.events import attach_server_tap, detach_server_tap
from vinga_server.events.live import LiveEvents
from vinga_server.registry import SessionRegistry


def test_healthz_reports_ok_and_version() -> None:
    client = TestClient(create_app(Config()))
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "revision": revision(),
    }


def test_healthz_names_the_build_and_not_only_the_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two answer different questions, and the version has been the
    same string since the package skeleton, so it is the revision that
    distinguishes one deploy from another (#41)."""
    revision.cache_clear()
    monkeypatch.setenv(REVISION_ENV, "deadbeefcafe")
    try:
        body = TestClient(create_app(Config())).get("/healthz").json()
    finally:
        revision.cache_clear()
    assert body["revision"] == "deadbeefcafe"
    assert body["version"] == __version__


# Readiness


class Unreachable:
    """A dependency that fails the test on any attribute access at all.

    What makes the non-coupling claim a positive one rather than an
    absence: a readiness handler that so much as looked at a provider or
    an MCP server fails here, whatever it went on to conclude.
    """

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"readiness reached for a dependency: .{name}")


def serving(registry: SessionRegistry) -> TestClient:
    """A described application with a composition on its state.

    `app.state.composition` is the seam the lifespan itself writes and
    the drain and the probes read, so a composition put there directly is
    the real seam rather than a way around one. Nothing is opened and
    nothing is built: the registry is real, because readiness is entitled
    to read it, and everything else refuses to be read at all.
    """
    app = create_app(Config())
    app.state.composition = SimpleNamespace(
        sessions=registry,
        runtime_factory=Unreachable(),
        mcp_servers=Unreachable(),
        memory=Unreachable(),
        conversations=Unreachable(),
    )
    return TestClient(app)


def test_readiness_survives_dependencies_that_stopped_answering() -> None:
    """A provider that will not answer and an MCP server that dropped off
    the network are recoverable, and they are diagnosed elsewhere
    (`doctor`, the configuration API's status read, the event stream).
    Failing readiness on them would take a pod out of the traffic set for
    something the next conversation might not even meet."""
    response = serving(SessionRegistry(max_sessions=1)).get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_an_app_that_is_not_serving_has_nothing_to_admit_to() -> None:
    """A `TestClient` outside its context manager runs no lifespan, which
    is the same state an external ASGI runner with the lifespan protocol
    off leaves, and the state a served application is in once its
    teardown has released everything."""
    response = TestClient(create_app(Config())).get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_a_full_server_is_not_ready_and_is_ready_again_when_a_slot_frees() -> None:
    """Readiness at capacity dips and recovers, which is what readiness
    is for: a refused device retries on its own, and an orchestrator
    withholding new traffic from a full pod is the behavior being asked
    for."""
    registry = SessionRegistry(max_sessions=1)
    client = serving(registry)
    session = cast(Any, object())
    assert registry.try_add(session)

    full = client.get("/readyz")
    assert full.status_code == 503
    assert full.json() == {"status": "full"}

    registry.remove(session)
    assert client.get("/readyz").json() == {"status": "ok"}


# A credential-shaped value, in the one place a probe URL can carry one.
PROBE_SENTINEL = "sk-live-probe-4b71ce"


async def test_a_value_in_a_probe_url_comes_back_on_no_surface(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both spellings of both probes answer, and none of them quotes the
    request back.

    A probe URL is what goes into an orchestrator manifest, a compose
    healthcheck and a CI script, so a token pasted into its query is an
    ordinary mistake rather than an exotic one. The router's own
    trailing-slash redirect used to put exactly that in a `Location`
    header, together with the Host it was asked on, which is what a
    proxy log and a browser history keep.

    Asserted over every surface this server has, because the claim is
    "nowhere": the headers, the body, the log in each rendering a
    deployment keeps it in, and the live event stream.
    """
    hub = LiveEvents()
    attach_server_tap(hub)
    watching = hub.subscribe()
    client = serving(SessionRegistry(max_sessions=1))
    try:
        with caplog.at_level("DEBUG"):
            answers = [
                client.get(f"{path}?token={PROBE_SENTINEL}", follow_redirects=False)
                for path in ("/readyz", "/readyz/", "/healthz", "/healthz/")
            ]
    finally:
        detach_server_tap(hub)

    # Answered rather than redirected, which is what leaves no header to
    # carry anything.
    assert [answer.status_code for answer in answers] == [200, 200, 200, 200]
    for answer in answers:
        headers = "\n".join(f"{name}: {value}" for name, value in answer.headers.items())
        assert PROBE_SENTINEL not in headers
        assert PROBE_SENTINEL not in answer.text
    assert PROBE_SENTINEL not in both_formats(caplog)
    streamed: list[Any] = []
    while (item := await watching.next(timeout=0)) is not None:
        streamed.append(item)
    assert PROBE_SENTINEL not in repr(streamed)


def test_a_server_on_its_way_out_says_draining_and_not_full() -> None:
    """The two can hold at once, and the terminal one is what a probe
    reports: a full server has a slot again when a conversation ends, and
    a draining one never admits another."""
    registry = SessionRegistry(max_sessions=1)
    assert registry.try_add(cast(Any, object()))
    registry.stop_admitting()

    response = serving(registry).get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "draining"}
