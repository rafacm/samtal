"""Integration lane scaffold.

From M3 on, this lane runs the xiaozhi-sdk device simulator against a live
server. Until then it verifies the ASGI app boots and serves end to end.
"""

from fastapi.testclient import TestClient

from samtal_server.app import app


def test_served_app_responds() -> None:
    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
