import pytest

from samtal_server.app import create_app
from samtal_server.config import Config


def test_given_config_is_the_one_the_app_serves() -> None:
    config = Config(server={"port": 9999})
    app = create_app(config)
    assert app.state.config is config


def test_app_without_a_config_loads_one() -> None:
    app = create_app()
    assert isinstance(app.state.config, Config)


def test_unknown_module_attribute_still_raises() -> None:
    import samtal_server.app as module

    # Indirect so ruff sees neither a useless expression (B018) nor a
    # constant getattr (B009); the point is the module __getattr__ fallback.
    missing = "nonexistent"
    with pytest.raises(AttributeError):
        getattr(module, missing)
