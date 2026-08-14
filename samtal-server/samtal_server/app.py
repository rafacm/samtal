import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import FastAPI

from samtal_server import __version__, onboarding, ota, ws
from samtal_server.auth import build_device_auth
from samtal_server.build_info import revision
from samtal_server.capture import CaptureStore, DeviceFacts
from samtal_server.config import Config
from samtal_server.config.api import api_token, build_api, mount_api
from samtal_server.config.boot import load_boot_config, reload_domain_config
from samtal_server.config.secrets import SecretStore
from samtal_server.device.bindings import DeviceBindings
from samtal_server.filler import build_agent_fillers
from samtal_server.providers import build_agent_providers
from samtal_server.registry import SessionRegistry
from samtal_server.runtime import prompt
from samtal_server.runtime.pipeline import bespoke_runtime_factory
from samtal_server.tools.mcp import McpReload, McpServers
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
    failing the boot.

    The way out also disposes the device bindings' read engine, the one
    thing on a running server that still holds the configuration
    database open."""
    app.state.agent_fillers.update(
        await build_agent_fillers(app.state.config, app.state.agent_providers)
    )
    await app.state.mcp_servers.start_all()
    try:
        yield
    finally:
        await app.state.mcp_servers.stop_all()
        # The one database connection pool a running server holds, let
        # go here so a process on its way out leaves no handle on the
        # data volume.
        app.state.bindings.dispose()


def _mcp_reloader(
    config: Config, servers: McpServers
) -> Callable[[], Awaitable[McpReload]]:
    """What the configuration API's reload route calls.

    Closed over here because this is where both halves are known: the
    configuration this process booted on, whose `server` section the
    stored domain half is composed onto, and the managers that are
    running. The re-read goes to the registry as a plain function rather
    than being done here, which keeps the tools layer clear of the
    database and leaves the registry deciding where a blocking read runs
    (a worker thread) and when it is allowed to run at all.
    """

    def read() -> tuple[Config, SecretStore]:
        reloaded = reload_domain_config(config)
        return reloaded.config, reloaded.secrets

    async def reload() -> McpReload:
        return await servers.reload(read)

    return reload


def _prompt_preview(
    config: Config, servers: McpServers, memory: MemoryStore | None
) -> Callable[[str], Awaitable[prompt.Assembled | None]]:
    """What the configuration API's prompt read calls.

    Closed over here for the reason the reload is: the three things an
    assembly needs (the configuration this process loaded, the MCP
    registry that is running, and the memory store this server writes)
    are known at the composition root and nowhere else, and the API
    application must not learn what a prompt is made of.

    An agent this server did not load answers None rather than raising,
    so the route decides what a missing one means. The guidance is read
    on the loop that owns the managers, before any await; the memory
    read is filesystem I/O and goes to a worker thread, which is what
    keeps an inspection request off the loop every live conversation is
    on.
    """

    async def assemble(agent: str) -> prompt.Assembled | None:
        if agent not in config.agents:
            return None
        half = prompt.know_how(
            config.prompt_for_agent(agent),
            config.fragments_for_agent(agent),
            servers.guidance_for_agent(agent),
        )
        if memory is None:
            return half
        return prompt.with_memory(half, await asyncio.to_thread(memory.read, agent))

    return assemble


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
    # Which agents a device may talk to, and the only thing this server
    # re-reads while it runs: an operator binds a board with the board
    # in front of them, and its next check-in is seconds away. Built
    # here because boot has already migrated the database, so nothing on
    # a device path ever has to; disposed in the lifespan above.
    app.state.bindings = DeviceBindings.open(app.state.config)
    # The devices waiting to be claimed, and the codes they are showing.
    # Runtime state owned by this app and shared with the configuration
    # API below, which is where a code becomes a binding. Always built,
    # even with onboarding off, so no handler needs a branch for its
    # absence; with onboarding off nothing ever puts anything in it.
    app.state.pending = onboarding.PendingDevices()
    # The configuration API's token, resolved here rather than at the
    # call below and for the reason it always was: the API is always
    # mounted, so a deployment that forgot the variable must be refused
    # before anything else is built rather than serve an admin surface
    # its own operator cannot reach. Resolving it inside the call would
    # have put the MCP servers' own refusals in front of it and changed
    # which failure such a deployment reads. Held in a local, passed
    # straight into the gate, and kept nowhere else, least of all on
    # app.state, and never logged.
    token = api_token(app.state.config)
    # Built before the API rather than beside the providers below,
    # because the API's status read reports these managers and they have
    # to exist to be handed over. An unknown reference or an unset
    # secret is still a boot failure here, exactly as it was; being
    # unreachable is still not one.
    app.state.mcp_servers = McpServers.build(app.state.config, secrets)
    # Absent memory configuration means no remember tool and no
    # injection; the directory itself is created on the first write.
    # Built before the API rather than beside the runtime below, because
    # the API's prompt read reports what a session would be sent and
    # memory is part of that.
    memory = app.state.config.memory
    app.state.memory = None if memory is None else MemoryStore(memory.dir)
    # The agents go with the token because a device write's
    # acknowledgement says whether the device can reach what it was just
    # bound to, and only this server knows what it loaded; the pending
    # table goes with it because claiming a code is how a device is
    # bound; and the MCP managers go with it because the status read
    # reports what they are doing, and passing the same object is what
    # makes that a report rather than a snapshot of what was true when
    # the API was built. The reload goes with them, because applying a
    # fresh read to those managers is the one action that namespace
    # serves, and the prompt assembly goes with them because what it
    # answers is what a session opening now would be sent.
    api = build_api(
        token,
        app.state.config.server.database.dir,
        app.state.config.agents,
        app.state.pending,
        app.state.mcp_servers,
        _mcp_reloader(app.state.config, app.state.mcp_servers),
        _prompt_preview(app.state.config, app.state.mcp_servers, app.state.memory),
    )
    # One registry per app: what decides whether there is room for the
    # next conversation, and what the drain reaches the live ones through.
    app.state.sessions = SessionRegistry(app.state.config.server.limits.max_sessions)
    # Built here so a bad provider configuration (unknown type, bad option,
    # missing extra, agent without a full pipeline) fails the boot rather
    # than the first conversation. The MCP servers are built above, for
    # the same reason and one of their own.
    app.state.agent_providers = build_agent_providers(app.state.config, secrets)
    # Filled at startup by the lifespan above, since synthesis is async;
    # empty means no agent masks its latency, which is the default.
    app.state.agent_fillers = {}
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
    # decided before the config was read. A null path unmounts it, which
    # is how a deployment retires it once every board it serves has been
    # moved to the onboarding path below.
    if app.state.config.server.ota_path is not None:
        app.include_router(ota.build_router(app.state.config.server.ota_path))
    # The same handlers at the short alias an operator types into a
    # captive portal. Its key is derived from the device-auth secret, so
    # there is nothing to configure and nothing to store; with auth off
    # there is no secret and the route mounts keyless.
    if app.state.config.server.onboarding.enabled:
        key = onboarding.onboarding_key(app.state.config.server)
        app.include_router(onboarding.build_router(key))
    app.include_router(ws.router)

    # Mounted last, so the device-facing routes are what this app is
    # and the configuration API is one gated object hanging off it. It
    # is control plane: it accepts inbound requests and sends nothing
    # anywhere, so server.local_only has nothing to say about it.
    mount_api(app, api)

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
