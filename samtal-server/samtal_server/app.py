import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI

from samtal_server import __version__, ota, ws
from samtal_server.auth import build_device_auth
from samtal_server.build_info import revision
from samtal_server.capture import CaptureStore, DeviceFacts
from samtal_server.config import Config
from samtal_server.config.boot import load_boot_config
from samtal_server.config.secrets import SecretStore
from samtal_server.filler import build_agent_fillers
from samtal_server.providers import build_agent_providers
from samtal_server.registry import SessionRegistry
from samtal_server.runtime.pipeline import bespoke_runtime_factory
from samtal_server.tools.mcp import McpServers
from samtal_server.tools.memory import MemoryStore

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect the configured MCP servers while the app runs, and close
    them on the way out so stdio child processes do not outlive the
    server. A server that will not connect only logs a warning.

    The filler clips are synthesized here rather than in create_app
    because synthesis is async and create_app is not: startup is still
    before the first conversation, which is what "at boot" is for. An
    agent whose synthesis fails runs with the feature off rather than
    failing the boot."""
    app.state.agent_fillers.update(
        await build_agent_fillers(app.state.config, app.state.agent_providers)
    )
    await app.state.mcp_servers.start_all()
    try:
        yield
    finally:
        await app.state.mcp_servers.stop_all()


def create_app(config: Config | None = None, secrets: SecretStore | None = None) -> FastAPI:
    """Build the ASGI app. Without a config the whole boot configuration is
    read here (the file named by SAMTAL_CONFIG plus the domain half from the
    database), which is what an external ASGI server gets; the CLI reads it
    itself (it also honours --config) and passes both halves in.

    `secrets` are the stored credentials the snapshot was loaded with,
    None for a configuration whose credentials are all environment
    references. They reach exactly the two places a credential is
    materialized: building a provider, and connecting an MCP server."""
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
    if config is None:
        booted = load_boot_config()
        config, secrets = booted.config, booted.secrets
    app.state.config = config
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
    app.state.agent_providers = build_agent_providers(app.state.config, secrets)
    # Filled at startup by the lifespan above, since synthesis is async;
    # empty means no agent masks its latency, which is the default.
    app.state.agent_fillers = {}
    app.state.mcp_servers = McpServers.build(app.state.config, secrets)
    # Absent memory configuration means no remember tool and no
    # injection; the directory itself is created on the first write.
    memory = app.state.config.memory
    app.state.memory = None if memory is None else MemoryStore(memory.dir)
    # How one conversation is built for one connection, closed over
    # once here: the providers, the MCP servers, the memory store and
    # the filler clips all outlive any single websocket, and a device
    # session should not have to name them to get a conversation. Built
    # after all four exist, and after the mutable fillers dict, which
    # the lifespan fills at startup and this closure sees fill.
    app.state.runtime_factory = bespoke_runtime_factory(
        app.state.config,
        app.state.agent_providers,
        app.state.mcp_servers,
        app.state.memory,
        app.state.agent_fillers,
    )
    # What a device says about itself at OTA check-in, kept for the
    # session that follows: a capture manifest needs the firmware
    # version, and the websocket handshake never carries it.
    app.state.device_facts = DeviceFacts()
    # Absent unless capture is configured and switched on, which is what
    # keeps recording something an operator has to ask for.
    capture = app.state.config.server.capture
    app.state.capture = (
        None
        if capture is None or not capture.enabled
        else CaptureStore(
            capture.dir, capture.max_session_s, capture.max_total_mb, capture.min_free_mb
        )
    )
    if app.state.capture is not None:
        logger.warning(
            "session capture is on: room audio and transcripts are being written to %s",
            capture.dir,
            extra={"event": "capture_enabled", "path": str(capture.dir)},
        )
    elif capture is not None:
        # Said out loud, because a configured section that records
        # nothing is otherwise a silence an operator has to debug.
        logger.info(
            "session capture is configured but off; set server.capture.enabled "
            "to record to %s",
            capture.dir,
            extra={"event": "capture_disabled", "path": str(capture.dir)},
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        # `version` is what this is, `revision` is which build of it.
        # A pod reporting only the former cannot be matched to the image
        # tag that produced it without going and asking the cluster.
        return {"status": "ok", "version": __version__, "revision": revision()}

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
