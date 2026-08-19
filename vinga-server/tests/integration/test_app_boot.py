"""Integration lane scaffold.

From M3 on, this lane runs the xiaozhi-sdk device simulator against a live
server. Until then it verifies the ASGI app boots and serves end to end.

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
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", str(tmp_path / "db"))
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
