"""Integration lane scaffold.

From M3 on, this lane runs the xiaozhi-sdk device simulator against a live
server. Until then it verifies the ASGI app boots and serves end to end.

The two probes are here for the cases that need a composition to exist:
readiness while serving, readiness through a drain with liveness
unchanged beside it, and one application read before, during and after
its lifespan. The cases that need no lifespan are the unit lane's, in
`tests/unit/test_health.py`.

It is also the one test that boots the way an external ASGI server does,
through the module-level `app`, which reads both halves itself: the file
half from the environment, and the domain half from the database that
half names. The database here is empty, which is a configuration: a
server with no agents serves its endpoints and turns every device away.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_served_app_responds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Imported here, not at module level: the attribute access is what
    # builds the app, and it has to happen after the override is set.
    from vinga_server.app import app

    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # The configuration API is mounted on the same app and gated,
        # which a boot through the module-level object is the honest
        # place to check: the token it compares against was resolved
        # from the environment that boot read.
        assert client.get("/api").status_code == 401
        assert client.get("/api/config").status_code == 401


def test_a_composed_server_is_ready_for_a_conversation() -> None:
    from vinga_server.app import app

    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_a_draining_server_stays_alive_while_it_stops_being_ready() -> None:
    """The criterion the split exists for, in one pair of reads: a
    process that has begun to shut down is still alive, and is finishing
    the conversations it has rather than taking another."""
    from vinga_server.app import app

    with TestClient(app, follow_redirects=False) as client:
        assert client.get("/readyz").status_code == 200
        alive = client.get("/healthz")

        # The registry this server admits through, drained on the loop it
        # is serving on: zero grace, and no conversation in flight to
        # spend it on.
        client.portal.call(app.state.composition.sessions.drain, 0.0)

        draining = client.get("/readyz")
        assert draining.status_code == 503
        assert draining.json() == {"status": "draining"}

        still_alive = client.get("/healthz")
        assert still_alive.status_code == 200
        assert still_alive.json() == alive.json()


def test_readiness_belongs_to_the_lifespan_that_can_answer_it() -> None:
    """One application through all three phases. The last one is what
    nothing else can show: an app whose teardown has released its
    engines, its writer and its providers must not answer that it is
    ready to be handed a conversation.

    A deployed prober never meets the first phase, because uvicorn binds
    its listener only once the lifespan's startup has finished, so a
    probe during a build gets a connection failure instead. Both ends
    report the same word because from outside they are the same fact:
    there is no serving composition here.
    """
    from vinga_server.app import app

    probe = TestClient(app)
    before = probe.get("/readyz")
    assert before.status_code == 503
    assert before.json() == {"status": "unavailable"}

    with TestClient(app) as client:
        assert client.get("/readyz").json() == {"status": "ok"}

    after = probe.get("/readyz")
    assert after.status_code == 503
    assert after.json() == {"status": "unavailable"}
