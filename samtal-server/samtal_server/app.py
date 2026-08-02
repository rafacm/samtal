from fastapi import FastAPI

from samtal_server import __version__
from samtal_server.config import Config, load_config


def create_app(config: Config | None = None) -> FastAPI:
    """Build the ASGI app. Without a config the file named by SAMTAL_CONFIG
    is loaded, which is what an external ASGI server gets; the CLI loads the
    config itself (it also honours --config) and passes it in."""
    app = FastAPI(title="samtal-server", version=__version__)
    app.state.config = config if config is not None else load_config()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


def __getattr__(name: str) -> FastAPI:
    """Build the module-level `app` on first access, so that `uvicorn
    samtal_server.app:app` still works while importing create_app (as the CLI
    does) neither loads the config twice nor turns a config error into an
    import traceback."""
    if name == "app":
        instance = create_app()
        globals()["app"] = instance
        return instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
