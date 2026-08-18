import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import FastAPI

from samtal_server import __version__, onboarding, ota, ws
from samtal_server.auth import build_device_auth
from samtal_server.build_info import revision
from samtal_server.capture import CaptureStore, DeviceFacts
from samtal_server.composition import Composition
from samtal_server.config import Config
from samtal_server.config.api import api_token, build_api, mount_api
from samtal_server.config.boot import load_boot_config, reload_domain_config
from samtal_server.config.responses import McpReloader, McpReloadResult
from samtal_server.config.secrets import SecretStore
from samtal_server.conversations import ConversationStore, migrate_existing
from samtal_server.device.bindings import DeviceBindings
from samtal_server.events import ServerEvents, resolve_enforcement
from samtal_server.filler import AgentFillers, build_agent_fillers
from samtal_server.providers import build_agent_providers
from samtal_server.registry import SessionRegistry
from samtal_server.runtime import prompt
from samtal_server.runtime.pipeline import bespoke_runtime_factory
from samtal_server.tools.mcp import McpServers
from samtal_server.tools.memory import MemoryStore

events = ServerEvents(__name__)


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

    The conversation store's writer thread lives exactly as long as this:
    `create_app` built the store cold (the file open and migrated, no
    thread), and it is started first and stopped last. First because
    everything after it is startup work that can fail, and the writer
    must not be left running behind a boot that never finished; last
    because the drain of what is queued belongs after every session has
    stopped producing. `stop()` is idempotent, so an app that never
    entered this leaks nothing either.

    The way out also disposes the device bindings' read engine, the one
    thing on a running server that still holds the configuration
    database open.

    What this starts and stops is bound once from the composition
    `create_app` built, so the startup reads the same declared fields
    every handler does."""
    comp: Composition = app.state.composition
    conversations = comp.conversations
    try:
        # Inside the guard rather than in front of it: a writer whose
        # thread will not start is exactly the case where the stop below
        # has to run anyway, and it is one line to let the same `finally`
        # cover it as covers everything after it.
        if conversations is not None:
            conversations.start()
        comp.agent_fillers.fill(await build_agent_fillers(comp.config, comp.agent_providers))
        await comp.mcp_servers.start_all()
        try:
            yield
        finally:
            await comp.mcp_servers.stop_all()
            # The one database connection pool a running server holds,
            # let go here so a process on its way out leaves no handle on
            # the data volume.
            comp.bindings.dispose()
    finally:
        if conversations is not None:
            conversations.stop()


def _mcp_reloader(config: Config, servers: McpServers) -> McpReloader:
    """What the configuration API's reload route calls.

    Closed over here because this is where both halves are known: the
    configuration this process booted on, whose `server` section the
    stored domain half is composed onto, and the managers that are
    running. The re-read goes to the registry as a plain function rather
    than being done here, which keeps the tools layer clear of the
    database and leaves the registry deciding where a blocking read runs
    (a worker thread) and when it is allowed to run at all.

    What comes back is the endpoint's whole answer, composed by the
    registry: this is the wiring between an application that must not
    load the MCP layer and a layer that must not know what a route is,
    and a shape either of them had to take apart would be a third
    place that knew both.
    """

    def read() -> tuple[Config, SecretStore]:
        reloaded = reload_domain_config(config)
        return reloaded.config, reloaded.secrets

    async def reload() -> McpReloadResult:
        return await servers.reload_result(read)

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
    # How strictly this process holds its events to their declarations
    # (#155), resolved here rather than at import because a running
    # server is a deployment whatever launched it: a production process
    # may import this function and serve the app under an ASGI runner
    # without ever reaching `main()`, and it must get the forgiving mode
    # a deployment needs rather than the strict default a lane wants.
    # First, before anything that could emit, and it refuses an
    # unusable value here rather than at the first live violation.
    resolve_enforcement()
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
    # Everything below is built into a local and assembled into one
    # `Composition` at the end: what a running server is made of is a
    # declared object (`composition.py`), and `app.state` carries that
    # one attribute rather than an attribute per resource.
    #
    # Auth is resolved first and fails the boot when it is enabled with no
    # secret in the environment, so a deployment that forgot one never
    # comes up serving every device that connects.
    device_auth = build_device_auth(config)
    # Which agents a device may talk to, and the only thing this server
    # re-reads while it runs: an operator binds a board with the board
    # in front of them, and its next check-in is seconds away. Built
    # here because boot has already migrated the database, so nothing on
    # a device path ever has to; disposed in the lifespan above.
    bindings = DeviceBindings.open(config)
    # The devices waiting to be claimed, and the codes they are showing.
    # Runtime state owned by this app and shared with the configuration
    # API below, which is where a code becomes a binding. Always built,
    # even with onboarding off, so no handler needs a branch for its
    # absence; with onboarding off nothing ever puts anything in it.
    pending = onboarding.PendingDevices()
    # The configuration API's token, resolved here rather than at the
    # call below and for the reason it always was: the API is always
    # mounted, so a deployment that forgot the variable must be refused
    # before anything else is built rather than serve an admin surface
    # its own operator cannot reach. Resolving it inside the call would
    # have put the MCP servers' own refusals in front of it and changed
    # which failure such a deployment reads. Held in a local, passed
    # straight into the gate, and kept nowhere else, least of all on
    # app.state, and never logged.
    token = api_token(config)
    # Built before the API rather than beside the providers below,
    # because the API's status read reports these managers and they have
    # to exist to be handed over. An unknown reference or an unset
    # secret is still a boot failure here, exactly as it was; being
    # unreachable is still not one.
    mcp_servers = McpServers.build(config, secrets)
    # Absent memory configuration means no remember tool and no
    # injection; the directory itself is created on the first write.
    # Built before the API rather than beside the runtime below, because
    # the API's prompt read reports what a session would be sent and
    # memory is part of that.
    memory_section = config.memory
    memory = None if memory_section is None else MemoryStore(memory_section.dir)
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
        config.server.database.dir,
        config.agents,
        pending,
        mcp_servers,
        _mcp_reloader(config, mcp_servers),
        _prompt_preview(config, mcp_servers, memory),
    )
    # One registry per app: what decides whether there is room for the
    # next conversation, and what the drain reaches the live ones through.
    sessions = SessionRegistry(config.server.limits.max_sessions)
    # Built here so a bad provider configuration (unknown type, bad option,
    # missing extra, agent without a full pipeline) fails the boot rather
    # than the first conversation. The MCP servers are built above, for
    # the same reason and one of their own.
    agent_providers = build_agent_providers(config, secrets)
    # Filled at startup by the lifespan above, since synthesis is async;
    # empty means no agent masks its latency, which is the default. The
    # cache is handed out here and filled there, and answers "nothing for
    # this agent" the whole time in between.
    agent_fillers = AgentFillers()
    # What was said, kept where it can be queried. Absent unless the
    # section exists and says so, which is what keeps recording a
    # conversation something an operator asks for.
    #
    # Built cold: the constructor opens and migrates the file, so a
    # directory the server cannot write fails the boot rather than the
    # first conversation, and the writer thread is the lifespan's to
    # start and stop. Built before the runtime factory below, because
    # that closure is how a turn's record reaches it.
    conversations_section = config.server.conversations
    database_dir = config.server.database.dir
    conversations = (
        None
        if conversations_section is None or not conversations_section.enabled
        else ConversationStore(
            database_dir,
            metrics=conversations_section.metrics,
            text=conversations_section.text,
            retention_days=conversations_section.retention_days,
        )
    )
    if conversations is None:
        # Recording off still leaves what was recorded readable: an
        # upgraded deployment that recorded last month serves its history
        # against the schema this server reads with. Migration is
        # maintenance of what exists and never creation, so a server that
        # was not asked for a store still leaves no file behind.
        migrate_existing(database_dir)
    # How one conversation is built for one connection, closed over
    # once here: the providers, the MCP servers, the memory store and
    # the filler clips all outlive any single websocket, and a device
    # session should not have to name them to get a conversation. Built
    # after all four exist, and after the mutable fillers dict, which
    # the lifespan fills at startup and this closure sees fill. The
    # store goes with them for the same reason: it outlives every
    # connection, and the per-session recorder is derived from it here.
    runtime_factory = bespoke_runtime_factory(
        config,
        agent_providers,
        mcp_servers,
        memory,
        agent_fillers,
        conversations,
    )
    # What a device says about itself at OTA check-in, kept for the
    # session that follows: a capture manifest needs the firmware
    # version, and the websocket handshake never carries it.
    device_facts = DeviceFacts()
    # Absent unless capture is configured and switched on, which is what
    # keeps recording something an operator has to ask for.
    capture_section = config.server.capture
    capture = (
        None
        if capture_section is None or not capture_section.enabled
        else CaptureStore(
            capture_section.dir,
            capture_section.max_session_s,
            capture_section.max_total_mb,
            capture_section.min_free_mb,
        )
    )
    if capture is not None:
        # Room audio is the whole of what makes this a recording, and the
        # decision track beside it is what the events already are. It
        # used to say "transcripts", which was true while the events
        # carried them; the narrowing (#120) left that half of the
        # sentence describing nothing the capture writes, and a warning
        # about what reaches a disk has to be exact in both directions.
        events.warning(
            "session capture is on: room audio and a track of the session's events "
            "are being written to %s",
            capture_section.dir,
            event="capture_enabled",
            path=str(capture_section.dir),
        )
    elif capture_section is not None:
        # Said out loud, because a configured section that records
        # nothing is otherwise a silence an operator has to debug.
        events.info(
            "session capture is configured but off; set server.capture.enabled "
            "to record to %s",
            capture_section.dir,
            event="capture_disabled",
            path=str(capture_section.dir),
        )
    # The one thing on this app's state, and the whole of what a handler
    # reads back: the fields are declared and typed in `composition.py`,
    # and the API's own request-time pieces ride along as the object the
    # sub-application already carries. The token is the standing
    # exception and is not here: it was passed into the gate above and is
    # held nowhere.
    app.state.composition = Composition(
        config=config,
        device_auth=device_auth,
        bindings=bindings,
        pending=pending,
        mcp_servers=mcp_servers,
        memory=memory,
        sessions=sessions,
        agent_providers=agent_providers,
        agent_fillers=agent_fillers,
        conversations=conversations,
        runtime_factory=runtime_factory,
        device_facts=device_facts,
        capture=capture,
        api=api.state.api_runtime,
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
    if config.server.ota_path is not None:
        app.include_router(ota.build_router(config.server.ota_path))
    # The same handlers at the short alias an operator types into a
    # captive portal. Its key is derived from the device-auth secret, so
    # there is nothing to configure and nothing to store; with auth off
    # there is no secret and the route mounts keyless.
    if config.server.onboarding.enabled:
        key = onboarding.onboarding_key(config.server)
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
