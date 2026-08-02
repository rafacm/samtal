from fastapi import FastAPI

from samtal_server import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="samtal-server", version=__version__)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
