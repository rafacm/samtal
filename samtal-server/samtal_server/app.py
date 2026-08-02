import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI

from samtal_server import __version__, ota, ws
from samtal_server.config import Config, load_config
from samtal_server.providers import build_agent_providers
from samtal_server.tools.mcp import McpServers


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect the configured MCP servers while the app runs, and close
    them on the way out so stdio child processes do not outlive the
    server. A server that will not connect only logs a warning."""
    await app.state.mcp_servers.start_all()
    try:
        yield
    finally:
        await app.state.mcp_servers.stop_all()


def create_app(config: Config | None = None) -> FastAPI:
    """Build the ASGI app. Without a config the file named by SAMTAL_CONFIG
    is loaded, which is what an external ASGI server gets; the CLI loads the
    config itself (it also honours --config) and passes it in."""
    app = FastAPI(title="samtal-server", version=__version__, lifespan=lifespan)
    app.state.config = config if config is not None else load_config()
    # Built here so a bad provider configuration (unknown type, bad option,
    # missing extra, agent without a full pipeline) fails the boot rather
    # than the first conversation. The same for MCP servers: an unknown
    # reference or an unset secret is a boot failure, being unreachable
    # is not.
    app.state.agent_providers = build_agent_providers(app.state.config)
    app.state.mcp_servers = McpServers.build(app.state.config)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(ota.router)
    app.include_router(ws.router)

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
