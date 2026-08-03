import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI

from samtal_server import __version__, ota, ws
from samtal_server.auth import build_device_auth
from samtal_server.config import Config, load_config
from samtal_server.providers import build_agent_providers
from samtal_server.registry import SessionRegistry
from samtal_server.tools.mcp import McpServers
from samtal_server.tools.memory import MemoryStore


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
    # No interactive docs, no schema. A device needs two paths and a
    # healthcheck needs a third; publishing an API description of them to
    # anyone who asks is surface with no reader, and the security default
    # is that nothing beyond what a device needs is exposed.
    app = FastAPI(
        title="samtal-server",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = config if config is not None else load_config()
    # Auth is resolved first and fails the boot when it is enabled with no
    # secret in the environment, so a deployment that forgot one never
    # comes up serving every device that connects.
    app.state.device_auth = build_device_auth(app.state.config)
    # One registry per app: what decides whether there is room for the
    # next conversation, and what the drain reaches the live ones through.
    app.state.sessions = SessionRegistry(app.state.config.server.limits.max_sessions)
    # Built here so a bad provider configuration (unknown type, bad option,
    # missing extra, agent without a full pipeline) fails the boot rather
    # than the first conversation. The same for MCP servers: an unknown
    # reference or an unset secret is a boot failure, being unreachable
    # is not.
    app.state.agent_providers = build_agent_providers(app.state.config)
    app.state.mcp_servers = McpServers.build(app.state.config)
    # Absent memory configuration means no remember tool and no
    # injection; the directory itself is created on the first write.
    memory = app.state.config.memory
    app.state.memory = None if memory is None else MemoryStore(memory.dir)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # The OTA router is built here rather than imported ready-made: its
    # path is configuration, and a module-level router would have been
    # decided before the config was read.
    app.include_router(ota.build_router(app.state.config.server.ota_path))
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
