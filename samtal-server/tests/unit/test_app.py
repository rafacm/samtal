from pathlib import Path

import pytest

from samtal_server.app import create_app
from samtal_server.config import Config


def test_given_config_is_the_one_the_app_serves() -> None:
    config = Config(server={"port": 9999})
    app = create_app(config)
    assert app.state.composition.config is config


def test_app_without_a_config_loads_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both halves: the file half from the environment, and the domain
    half from the database it names."""
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    app = create_app()
    assert isinstance(app.state.composition.config, Config)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_the_interactive_docs_are_not_served(path: str) -> None:
    """A device needs two paths and a healthcheck a third. Publishing an
    API description of them to anyone who asks is surface with no
    reader."""
    from fastapi.testclient import TestClient

    assert TestClient(create_app(Config())).get(path).status_code == 404


def test_unknown_module_attribute_still_raises() -> None:
    import samtal_server.app as module

    # Indirect so ruff sees neither a useless expression (B018) nor a
    # constant getattr (B009); the point is the module __getattr__ fallback.
    missing = "nonexistent"
    with pytest.raises(AttributeError):
        getattr(module, missing)
