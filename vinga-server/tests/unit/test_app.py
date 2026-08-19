from pathlib import Path

import pytest

from tests.support.apps import entered_app
from vinga_server.app import create_app
from vinga_server.config import Config


def test_given_config_is_the_one_the_app_serves() -> None:
    config = Config(server={"port": 9999})
    with entered_app(config) as (app, _):
        assert app.state.composition.config is config


def test_app_without_a_config_loads_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both halves: the file half from the environment, and the domain
    half from the database it names. Read in the describe phase, since a
    configuration that will not load is a refusal whatever launched the
    app, and carried to the build on the seed."""
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    with entered_app() as (app, _):
        assert isinstance(app.state.composition.config, Config)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_the_interactive_docs_are_not_served(path: str) -> None:
    """A device needs two paths and a healthcheck a third. Publishing an
    API description of them to anyone who asks is surface with no
    reader."""
    from fastapi.testclient import TestClient

    assert TestClient(create_app(Config())).get(path).status_code == 404


def test_unknown_module_attribute_still_raises() -> None:
    import vinga_server.app as module

    # Indirect so ruff sees neither a useless expression (B018) nor a
    # constant getattr (B009); the point is the module __getattr__ fallback.
    missing = "nonexistent"
    with pytest.raises(AttributeError):
        getattr(module, missing)
