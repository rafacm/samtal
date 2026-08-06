import pytest
from fastapi.testclient import TestClient

from samtal_server import __version__
from samtal_server.app import create_app
from samtal_server.build_info import REVISION_ENV, revision


def test_healthz_reports_ok_and_version() -> None:
    client = TestClient(create_app())
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
        body = TestClient(create_app()).get("/healthz").json()
    finally:
        revision.cache_clear()
    assert body["revision"] == "deadbeefcafe"
    assert body["version"] == __version__
