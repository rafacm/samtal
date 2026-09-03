import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Collection
from dataclasses import dataclass

from fastapi import FastAPI

from vinga_server import __version__, logs, onboarding, ota, ws
from vinga_server.auth import build_device_auth
from vinga_server.build_info import revision
from vinga_server.capture import CaptureStore, DeviceFacts
from vinga_server.composition import Composition
from vinga_server.config import Config, ConfigError
from vinga_server.config.api import (
    api_token,
    build_api,
    build_api_runtime,
    installed,
    mount_api,
    open_store,
)
from vinga_server.config.boot import load_boot_config, reload_domain_config
from vinga_server.config.diff import Loaded, config_diff
from vinga_server.config.loader import (
    DatabaseBusyError,
    ProviderRefusedError,
    ReloadInProgressError,
    RunningConfigMovedError,
    StorageError,
)
from vinga_server.config.models import ServerConfig
from vinga_server.config.reload import ConfigReload
from vinga_server.config.responses import (
    ConfigDiff,
    ConfigDiffReader,
    ConfigReloader,
    ConfigReloadResult,
    RuntimeInfo,
)
from vinga_server.config.secrets import SecretStore
from vinga_server.conversations import ConversationStore, open_conversations, threads
from vinga_server.conversations.store import erasures_announced_to
from vinga_server.device.bindings import DeviceBindings
from vinga_server.events import ServerEvents, attach_server_tap, detach_server_tap
from vinga_server.events.catalog import CaptureDisabled, CaptureEnabled
from vinga_server.events.live import LiveEvents
from vinga_server.events.values import ConfiguredPath
from vinga_server.filler import build_agent_fillers
from vinga_server.generation import Generation, Generations
from vinga_server.memory.store import MemoryStore, open_memory, purge
from vinga_server.providers import ProviderError, build_world
from vinga_server.registry import SessionRegistry
from vinga_server.runtime import prompt
from vinga_server.runtime.pipeline import bespoke_runtime_factory
from vinga_server.tools.mcp import McpConfigError, McpServers

events = ServerEvents(__name__)

# What a boot that refused looks like from the outside, and the whole of
# what may be said about it. `DatabaseBusyError` is a `ConfigError`, so
# the three names below are the boot failure taxonomy in full: every one
# of them carries a message written to be printed as it is, which is what
# lets the bridge below hand one sentence to an operator and nothing
# else.
#
# `McpConfigError` is here because it is a boot refusal like the others
# and was reaching the operator like a bug: an entry missing its egress
# declaration under `server.local_only`, or naming an environment
# variable nothing sets, answered with uvicorn's traceback and exit code
# 3 rather than the sentence and exit code 1 its message was written for.
# It is a `ValueError` rather than a `ConfigError` and stays one, since
# the reload path catches it beside `ConfigError` by name and re-parenting
# it would change what a `ValueError` means to every caller of the MCP
# layer; naming it here is the smaller and more honest change.
BOOT_FAILURES = (ConfigError, McpConfigError, ProviderError)


class StartupFailed(RuntimeError):
    """A boot that refused, carried out of the lifespan as one sentence.

    Construction happens inside the lifespan (#142), which puts it inside
    uvicorn rather than in front of it, and uvicorn renders a lifespan
    exception as a traceback. A provider or configuration failure raised
    as itself would therefore print its exception chain to stderr, and a
    chain from this depth can carry a credential: a driver's message
    quotes the URL it could not reach, a client library's quotes what it
    was configured with.

    So the taxonomy is caught, its already-sanitized sentence is recorded
    on the seed for `main()` to print, and this is raised in its place,
    outside the `except` that caught it so that nothing is chained to it
    at all. What uvicorn can render is then this class and this sentence.
    Anything outside the taxonomy propagates as the bug it is.
    """


@dataclass
class _CompositionSeed:
    """What the describe phase leaves for the build phase.

    `create_app` describes the application (routes, the mounted API
    shell, the gate) and builds none of its resources; the lifespan
    builds them. This carries what the build needs across, and is
    deliberately the smallest thing that works: the configuration and the
    stored credentials it was loaded with, the mounted API application
    whose request-time pieces the build attaches, and the callback the
    CLI passes to say the server is up.

    The configuration API's token is NOT here. It is resolved in the
    describe phase and passed straight into the gate, which is the
    standing exception this project already made for it, and a copy on
    this object would be a second place it lives (the plan review's
    finding 10).

    `failure` is written by the lifespan when the build refuses: the
    sanitized sentence, for `main()` to print after `serve()` returns.
    """

    config: Config
    secrets: SecretStore | None
    api: FastAPI
    on_started: Callable[[], None] | None
    # Whether this configuration's domain half was read from a store, or
    # handed to `create_app` by whoever built it. It decides one thing:
    # whether the bindings view reads the database live or serves the
    # snapshot authoritatively. Stated here, where the composition is,
    # rather than probed later: the probe used to be whether a database
    # file existed, and a database is now always there by the time
    # anything could look (#283).
    from_store: bool = False
    failure: str | None = None


def startup_failure(app: FastAPI) -> str | None:
    """The sentence a refused startup left behind, if it refused.

    `main()`'s way of asking, after `serve()` has returned, whether the
    server ever came up. None means it did, or that whatever stopped it
    was not a boot failure and has already been raised as itself.
    """
    seed: _CompositionSeed | None = getattr(app.state, "seed", None)
    return None if seed is None else seed.failure


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build what this server is made of, hold it while it serves, and
    release it in reverse on the way out (#142).

    The build is here rather than in `create_app` because a resource
    should be acquired by the thing that will release it: an app that is
    described and never served now opens no database, starts no thread
    and loads no model, and every acquisition below is registered for
    release the moment it is made, so a failure part way through unwinds
    exactly what got as far as existing.

    Startup work that can fail is what makes that discipline load
    bearing: the providers load models, the MCP servers connect, and the
    filler clips are synthesized. A boot failure is caught here and
    carried out as `StartupFailed` with its sanitized sentence, so that
    an operator reads the same line they read when this ran in front of
    uvicorn.

    `on_started` is the CLI's banner, invoked once the build has
    succeeded and before the first request can arrive: a line announcing
    where to point a device must not be printed by a server that then
    fails to start.
    """
    seed: _CompositionSeed = app.state.seed
    async with contextlib.AsyncExitStack() as stack:
        failure: str | None = None
        try:
            await _build_composition(app, seed, stack)
        except BOOT_FAILURES as exc:
            # Recorded rather than re-raised from here: raising below,
            # with the `except` block already left, is what leaves the
            # replacement exception with no `__context__` to render.
            failure = str(exc)
        if failure is not None:
            seed.failure = failure
            raise StartupFailed(failure) from None
        if seed.on_started is not None:
            seed.on_started()
        yield


async def _build_composition(
    app: FastAPI, seed: _CompositionSeed, stack: contextlib.AsyncExitStack
) -> Composition:
    """Everything one running server is made of, built in order.

    The order is the one `create_app` documented when it did this work,
    and the comments came with it. What is new is the exit stack: each
    acquisition registers its release as it is made, so the unwinding is
    the reverse of the building and a partial startup leaves nothing
    open (the plan review's finding 6).
    """
    config, secrets = seed.config, seed.secrets
    # Everyone watching this server's events right now (#342), built
    # first so that the boot's own events reach whoever is watching the
    # next one: what an operator tails a redeploy for is exactly the
    # lines a hub attached at the end of the build would miss.
    #
    # The detach is registered in the same breath, and that is the whole
    # of why it is here rather than beside the emit sites. The server
    # tap set is process-global and outlives any one application, so an
    # attachment that survived this lifespan would deliver into a dead
    # app, and a second lifespan in the same process would deliver every
    # server event twice. The exit stack is what already unwinds a
    # partial startup, so the registration is on it rather than in a
    # `finally` that a failure part way through would skip.
    live = LiveEvents()
    attach_server_tap(live)
    stack.callback(detach_server_tap, live)
    # Auth is resolved first and fails the boot when it is enabled with no
    # secret in the environment, so a deployment that forgot one never
    # comes up serving every device that connects. `create_app` already
    # read it once for exactly that refusal; this is the issuer itself,
    # which belongs to the composition that holds it.
    device_auth = build_device_auth(config)
    # The devices waiting to be claimed, and the codes they are showing.
    # Runtime state owned by this app and shared with the configuration
    # API below, which is where a code becomes a binding. Always built,
    # even with onboarding off, so no handler needs a branch for its
    # absence; with onboarding off nothing ever puts anything in it.
    pending = onboarding.PendingDevices()
    # Built before the API's runtime rather than beside the providers
    # below, because the API's status read reports these managers and
    # they have to exist to be handed over. An unknown reference or an
    # unset secret is still a boot failure here, exactly as it was; being
    # unreachable is still not one.
    mcp_servers = McpServers.build(config, secrets)
    # Built here so a bad provider configuration (unknown type, bad option,
    # missing extra, agent without a full pipeline) fails the boot rather
    # than the first conversation. The MCP servers are built above, for
    # the same reason and one of their own.
    #
    # Through the one path an apply builds through, which is what makes
    # the two agree about ownership: each engine is constructed off the
    # loop, one at a time, because that is where a boot spends its time,
    # and a failure part way through closes what got as far as existing
    # rather than leaving it to a garbage collector this milestone has
    # just declared insufficient (#191). There is nothing to carry over
    # at a boot, which is the degenerate case of the same call.
    engines = await build_world(config, secrets)
    # Owned from the moment the build returns, and by this stack until
    # the holder below takes them on. What is between the two lines is
    # the conversation store's construction and migration, the writer's
    # start and the filler synthesis, every one of which can fail; a
    # build whose objects nothing owned across that stretch would leak
    # every engine it had just loaded.
    stack.push_async_callback(engines.close)
    # What was said, kept where it can be queried. Absent unless the
    # section exists and says so, which is what keeps recording a
    # conversation something an operator asks for.
    #
    # The constructor opens and migrates the schema, so a database the
    # server cannot reach fails the boot rather than the first
    # conversation. The stop is registered before the start below: a
    # writer whose thread will not start is exactly the case where the
    # stop has to run anyway.
    database = config.server.database
    # What an agent was asked to remember and what a conversation is
    # keeping, in a schema of its own beside the record's (#314). Opened
    # here, which is what migrates it, and before the conversation
    # writer below rather than after it, because that writer's retention
    # deletes a pruned thread's memory in its own transaction and holds
    # the seam for that from its first prune. The disposal is registered
    # in the same breath, so a boot that fails after this point unwinds
    # through the stack rather than leaving a pool nobody owns, and
    # registering it FIRST is what makes it unwind LAST: the writer
    # drains against a store that is still open.
    #
    # Unconditionally, and behind no section at all: migrating creates
    # an empty table, an empty table is not a memory, and an agent that
    # has been told nothing reads as the empty string and gets no block
    # in its prompt.
    memory = open_memory(database)
    stack.callback(memory.close)
    # And the other half of the deletion promise: a thread erased through
    # the operator API publishes what it took, and the memory store
    # refuses a write addressed to one of those threads from then on. The
    # subscription is wired here because this is where both sides exist,
    # and it is a context manager so that a partial startup and a second
    # application in one process both detach.
    #
    # In front of the writer below and therefore unwound behind it: a
    # deletion published while the writer is still draining has to reach
    # the store that is going to refuse the write it would otherwise
    # resurrect.
    stack.enter_context(erasures_announced_to(memory.threads_erased))
    conversations_section = config.server.conversations
    conversations = (
        None
        if conversations_section is None or not conversations_section.enabled
        else ConversationStore(
            database,
            metrics=conversations_section.metrics,
            text=conversations_section.text,
            retention_days=conversations_section.retention_days,
            # How retention takes the memory of the threads it prunes,
            # handed over rather than imported: the memory store reads
            # the record's own table, so naming it there would close a
            # cycle. It is the same function a deletion through the API
            # calls.
            purge_memory=purge,
        )
    )
    if conversations is None:
        # Recording off still leaves what was recorded readable: an
        # upgraded deployment that recorded last month serves its history
        # against the schema this server reads with. Migrating creates
        # empty tables, which is not a recording, and no writer is
        # started: what "recording off" means is that no row is written.
        open_conversations(database).dispose()
    else:
        stack.callback(conversations.stop)
    # The heal for what no transaction covers: state and held facts whose
    # thread has no row in the record and has not been written to for a
    # day. Pre-upgrade leftovers, threads that never landed a first turn,
    # and deployments that record nothing at all. Contained inside the
    # store, so a database that refuses it says so once and the boot
    # carries on, and off the loop because it is a database round trip
    # like any other.
    #
    # Here rather than beside the open above, and the reason is the
    # anti-join: what it asks is which of these threads the record has
    # never heard of, so the record's schema has to exist before it can
    # be asked. Whichever branch above ran has migrated it by now. Before
    # the writer starts, so the sweep and the writer's own first
    # retention pass do not reach for the same rows at once.
    await asyncio.to_thread(memory.sweep)
    if conversations is not None:
        # Started here rather than at the end of the build, in front of
        # everything a boot can still fail in: the stop above is
        # registered on the exit stack, so a failure after this point
        # unwinds through it, and a writer started later would be one
        # more window where a refusal leaves a thread behind.
        conversations.start()
    # The filled pauses, in each agent's own voice. Synthesized here
    # rather than beside the providers because synthesis is async, and
    # before the generation below because they are part of the world it
    # is: startup is still before the first conversation, which is what
    # "ahead of time" means. An agent whose synthesis fails runs with
    # the feature off rather than failing the boot.
    fillers = await build_agent_fillers(config, engines.world.agents)
    # The world new work binds, and the only place it is replaced. The
    # boot's configuration, the credentials loaded with it, the engines
    # built from the two and the clips those engines spoke are the first
    # generation; a reload composes the next one and installs it here.
    # An empty store rather than None for a deployment whose credentials
    # are all environment references, so that a generation is one shape
    # rather than two.
    generations = Generations(
        Generation(
            config,
            secrets if secrets is not None else SecretStore(),
            fillers.clips,
            # The transfer, in the statement that reads them: from here
            # the holder is what closes these, at the end of an apply
            # that replaced them or at the end of the process, and the
            # cleanup registered above becomes the no-op it says it is.
            engines.installed(),
        )
    )
    # And the close at the other end of the process, registered here so
    # that it unwinds after the drain below has asked every conversation
    # to finish: what a world holds is let go of at every end a world can
    # meet, an apply that replaced it and a server that is stopping
    # alike.
    stack.push_async_callback(generations.aclose)
    # Which agents a device is bound to, and the only thing this server
    # re-reads from storage while it runs: an operator binds a board
    # with the board in front of them, and its next check-in is seconds
    # away. Opened after the holder because it asks the holder what
    # world is being served, which is the fallback when the database
    # cannot be read, and because boot has already migrated the
    # database, so nothing on a device path ever has to.
    #
    # It decides once, and for the life of the process, whether there is
    # a database to read at all: a configuration whose domain half came
    # from one is served live, and one composed without a database is
    # served from the generation being served, authoritatively (see
    # `bindings.py`). Both production entry points compose from the
    # database, `main()` through `load_boot_config` and the ASGI one
    # through `create_app`, so a server always gets the live view; the
    # other shape is the test lane's and an embedded caller's.
    bindings = (
        DeviceBindings.open(generations)
        if seed.from_store
        else DeviceBindings.snapshot_only(generations)
    )
    stack.callback(bindings.dispose)
    # One registry per app: what decides whether there is room for the
    # next conversation, what the drain reaches the live ones through,
    # and which world each of them is holding, which is what says when a
    # replaced one may let go of its engines.
    sessions = SessionRegistry(config.server.limits.max_sessions, generations)
    # How one conversation is built for one connection, closed over
    # once here: the providers, the MCP servers and the memory store all
    # outlive any single websocket, and a device session should not have
    # to name them to get a conversation. The clips are not among them
    # and are read off the generation per connection, which is what
    # makes a re-synthesized one converge at the next session. The store
    # is closed over for the reason the first three are: it outlives
    # every connection, and the per-session recorder is derived from it
    # here.
    #
    # The read seam beside the store is the other direction through the
    # same database: what a resume reads a thread back through. Built
    # wherever there is a record to read, because it holds nothing and
    # opens nothing until it is asked; whether a conversation may be
    # resumed is the runtime's own read of the section, so the switch
    # has one home rather than a second one here.
    runtime_factory = bespoke_runtime_factory(
        generations,
        mcp_servers,
        memory,
        conversations,
        None if conversations is None else threads.Reads(database),
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
        events.emit(lambda: CaptureEnabled(path=ConfiguredPath(capture_section.dir)))
    elif capture_section is not None:
        # Said out loud, because a configured section that records
        # nothing is otherwise a silence an operator has to debug.
        events.emit(lambda: CaptureDisabled(path=ConfiguredPath(capture_section.dir)))
    # The live half of the configuration API, attached to the shell
    # `create_app` mounted. Starlette runs no lifespan for a mounted
    # application, so the objects its requests resolve are installed from
    # here: the agents this server loaded, because a device write's
    # acknowledgement says whether the device can reach what it was just
    # bound to; the pending table, because claiming a code is how a
    # device is bound; the MCP managers, because the status read reports
    # what they are doing, and passing the same object is what makes that
    # a report rather than a snapshot of what was true when the API was
    # built. The reload goes with them, because applying a fresh read to
    # those managers is the one action that namespace serves, and the
    # prompt assembly goes with them because what it answers is what a
    # session opening now would be sent. The diff read goes with them
    # because one of the two worlds it compares is this one: the
    # configuration this process booted and the MCP entries running
    # right now, which are not the same thing once a reload has been
    # applied.
    #
    # Before the yield and therefore before any request: uvicorn serves
    # nothing until this generator has yielded, so no request can see a
    # half-attached API.
    api_runtime = build_api_runtime(
        database,
        # Which agents this server can be asked for, as a question: an
        # apply installs the stored agent set, so a device write
        # acknowledged against a set captured here would name a restart
        # for an agent the reload before it had already installed.
        lambda: frozenset(generations.current().config.agents),
        pending,
        mcp_servers,
        config_reloader(
            generations,
            mcp_servers,
            lambda: reload_domain_config(config),
            sessions.held,
        ),
        _prompt_preview(generations, mcp_servers, memory),
        config_diff_reader(
            # One side of the comparison: what this process is serving,
            # read at the moment the comparison is composed rather than
            # captured here, because a reload replaces it.
            generations,
            mcp_servers,
            # And the other side, which is the reload's own re-read of
            # the stored half, run where the reload runs it.
            lambda: reload_domain_config(config),
        ),
        # Whether there is a store behind what this process serves. The
        # bindings view decided it once, at the open above, and it is
        # the same question: a server composed from a configuration it
        # was handed has no store describing its world. The two surfaces
        # that span both sides refuse in that mode, and a device write
        # says what it can honestly say.
        bindings.snapshot_authoritative,
        # And which deployment this is, resolved once here because none
        # of it moves: the build is this process's and the onboarding
        # URL is derived from the server section, which is the one part
        # of a configuration that is genuinely read once. Resolved here
        # rather than in the API for the reason the three callables above
        # it are: where a deployment's origin comes from and what its key
        # is derived from are this root's business, and the API is the
        # one surface that must go on being renderable with none of it.
        _runtime_identity(config.server),
        # And whoever is watching this server's events, which is the one
        # piece of the runtime the API shares with the device edge: the
        # same hub the sessions above emit into is what the stream route
        # subscribes a reader to (#342).
        live=live,
    )
    # The configuration database, opened once here rather than on every
    # request (#142), and migrated in the same call because nothing may
    # assume boot ran: an API-first deployment builds this application
    # over a database that has no schema yet, and the check is a cheap
    # no-op for one that has. The keys the store decrypts with are
    # derived in the same breath, inside the handle. A lock another
    # writer is holding refuses here, as the retryable database refusal,
    # which is part of the boot taxonomy the lifespan above turns into
    # one sentence.
    #
    # This is the mounted owner. Starlette runs no lifespan for a mounted
    # application, so the one `build_api` gave that application never
    # runs and there is no second engine to collide with.
    #
    # The handle is installed through `installed`, registered after the
    # open and therefore unwound before it, which is what keeps a request
    # arriving after teardown from finding a handle whose engine would
    # open fresh connections nobody owns.
    store = stack.enter_context(open_store(database))
    stack.enter_context(installed(api_runtime, store))
    seed.api.state.api_runtime = api_runtime
    # The one thing on this app's state a handler reads back: the fields
    # are declared and typed in `composition.py`, and the API's own
    # request-time pieces ride along as the object the sub-application
    # already carries. The token is the standing exception and is not
    # here: it was passed into the gate in `create_app` and is held
    # nowhere.
    composition = Composition(
        server=config.server,
        generations=generations,
        device_auth=device_auth,
        bindings=bindings,
        pending=pending,
        mcp_servers=mcp_servers,
        memory=memory,
        sessions=sessions,
        conversations=conversations,
        runtime_factory=runtime_factory,
        device_facts=device_facts,
        capture=capture,
        live=live,
        api=api_runtime,
    )
    # Connected last, and closed first on the way out so stdio child
    # processes do not outlive the server. A server that will not connect
    # only logs a warning. The stop is registered in front of the start
    # for the reason the store's is: what a start got part way through is
    # what a stop is for.
    stack.push_async_callback(mcp_servers.stop_all)
    await mcp_servers.start_all()
    # Installed last, and its removal registered in the same breath,
    # after every other registration on this stack. The unwind is last in
    # first out, so this is what runs first on the way out: the attribute
    # is gone before any resource behind it is released, and nothing can
    # read a composition whose parts are already closing. It is the
    # discipline the API's installed runtime state already follows, now
    # applied to the attribute the drain and the readiness probe read,
    # which is what makes a served-then-torn-down application answer that
    # it has no composition rather than that it is ready.
    app.state.composition = composition
    stack.callback(delattr, app.state, "composition")
    return composition


# What a refused reload says instead of what the store said.
#
# The same rule the comparison beside it follows, and for the same
# reason: what a reload refuses on is arbitrary stored state, and a
# sentence composed over it can quote a value that was written into the
# wrong field. A credential pasted where a model expects a name is
# exactly that shape of thing, and it is exactly the shape of thing a
# refusal is composed over.
#
# So the three sentences below are fixed, one per status a refused
# stored half can carry, and the types are the store's own, so
# `REFUSAL_STATUS` still decides the status. Where the location is
# available instead is a server started from this store, which refuses
# on the same state and prints the location it refused on.
_RELOAD_REFUSED = (
    "the reload was refused and nothing was changed: the stored configuration does not "
    "compose into a snapshot this server can serve, a credential stored in it will not "
    "open under the configured keys, or a server it names could not be built. Where "
    "exactly is deliberately not said here, because a sentence about a stored value is "
    "the one thing a reload's answer never carries. A server started from this store "
    "refuses on the same state and names the location it refused on."
)

_RELOAD_UNREADABLE = (
    "the reload was refused and nothing was changed: this server's configuration could "
    "not be read, for a reason that is not this request's. The failure is recorded in "
    "this server's log."
)

_RELOAD_DATABASE_BUSY = (
    "the configuration database is busy: another process holds the write lock, so the "
    "stored configuration could not be read. Nothing was changed; make the request "
    "again."
)


def config_reloader(
    generations: Generations,
    servers: McpServers,
    read: Callable[[], Loaded],
    held: Callable[[], Collection[Generation]] = tuple,
) -> ConfigReloader:
    """What the configuration API's reload route calls.

    Closed over here because this is where every piece is known: the
    holder whose generation a reload replaces, the managers that are
    running, who is still holding a world this apply may retire, and the
    re-read of the stored half, which goes to the apply as a plain
    function rather than being done here so that the layers below stay
    clear of the database and the apply decides where a blocking read
    runs.

    What comes back is the endpoint's whole answer, composed where the
    phases live: this is the wiring between an application that must not
    load the MCP layer or the runtime and a layer that must not know
    what a route is, and a shape either of them had to take apart would
    be a third place that knew both.

    A refused apply keeps its type and loses its words, the shape the
    comparison read below already has. The type is what the API turns
    into a status; the sentence is replaced by one of the three fixed
    ones above, because the apply composes its refusals over the stored
    state it refused on. Two refusals pass through as themselves, and
    for one reason: their sentences are this server's own and were
    composed over nothing at all. `ReloadInProgressError` is about this
    server's exclusion, and `ProviderRefusedError` is the apply's own
    fixed sentence about a world whose engines would not build, said
    where the failure is (`config/reload.py`) because that is where the
    class of it can be recorded in the log in the same breath. A failure
    with no type at all is a bug and is left alone, for the reason the
    comparison leaves one alone.
    """
    applying = ConfigReload(generations, servers, read, held)

    async def reload() -> ConfigReloadResult:
        """One apply, refused in this route's words rather than the
        store's.

        Built in the handler and raised after it, which is this
        codebase's rule and load bearing here: raised inside one, the
        replacement would carry the original as its context, and the
        original is what holds the stored bytes. Neither `__cause__` nor
        `__context__` reaches the caller, and the caller is a response
        body.
        """
        refusal: ConfigError | None = None
        try:
            return await applying.apply()
        except (ReloadInProgressError, ProviderRefusedError):
            raise
        except DatabaseBusyError:
            refusal = DatabaseBusyError(_RELOAD_DATABASE_BUSY)
        except StorageError:
            refusal = StorageError(_RELOAD_UNREADABLE)
        except ConfigError:
            refusal = ConfigError(_RELOAD_REFUSED)
        raise refusal

    return reload


# How many times one diff may read the stored half before it gives up:
# the first read and two more. A bound rather than a loop, because a
# world moving under every attempt is a server being reloaded in a tight
# cycle, and answering "ask again" then is more honest than reading a
# database until one of the attempts happens to win.
DIFF_LOADS = 3

# And what it says when it gives up. The whole of the advice is to make
# the request again: nothing is wrong, nothing was changed, and the next
# one lands in a world that is holding still unless the reloads keep
# coming.
CONFIG_MOVED = (
    "the running configuration changed while this comparison was being read, so the "
    "answer would have described two states that never existed together. Nothing was "
    "changed by this request; make it again."
)

# What a refused stored half says instead of what the store said.
#
# Most refusals this application answers with name the location they
# refused on, and that is the right contract for a surface whose answers
# already carry configuration. This read's answers do not: they are
# entity names and closed tokens, chosen so that there is nothing to
# filter. A sentence composed over stored state breaks that, and not
# only in theory, since a stored value that is not what its column is
# for is exactly the shape of thing that gets refused, and a credential
# pasted into the wrong field is exactly that shape of thing.
#
# So the three sentences below are fixed, one per status this read can
# refuse the stored half with, and each says where the location is
# available instead, which is a server started from this store. The
# apply beside this one refuses in the same shape and for the same
# reason. The types are the store's own, so `REFUSAL_STATUS` still
# decides the status.
_DIFF_REFUSED = (
    "the stored configuration cannot be compared with what this server is running: it "
    "does not compose into a valid snapshot, or a credential stored in it will not "
    "open under the configured keys. Where exactly is deliberately not said here, "
    "because this read answers with entity names and labels and a sentence about a "
    "stored value is the one thing it never carries. A server started from this store "
    "refuses on the same state and names the location it refused on."
)

_DIFF_UNREADABLE = (
    "the stored configuration could not be read, for a reason that is not this "
    "request's. The failure is recorded in this server's log."
)

_DIFF_DATABASE_BUSY = (
    "the configuration database is busy: another process holds the write lock, so the "
    "stored half could not be read. Nothing was changed; make the request again."
)


def config_diff_reader(
    generations: Generations, servers: McpServers, read: Callable[[], Loaded]
) -> ConfigDiffReader:
    """What the configuration API's diff read calls.

    Closed over here for the reason the reload is: the holder whose
    generation is what this server is serving, and the registry a reload
    replaces, are known at the composition root and nowhere else, and
    the API application must not learn which kind of configuration
    converges where.

    The running side is read from the holder at the moment the
    comparison is composed rather than captured when this is built. That
    is the whole of what makes the answer keep its promise once a reload
    can apply something: a comparison against the boot would report as
    pending exactly the changes an apply has already made, which is the
    one thing an answer about what is pending must not do.

    `read` is the stored half, handed in rather than done here, exactly
    as the reload hands its own re-read to the registry: opening a
    database belongs to the layer that owns one, and what this function
    owns is where that read runs and what makes its answer one world.
    The composition root passes `reload_domain_config`, which is the
    settled decision that the diff's stored side is the reload's own
    re-read: the same migration, the same verification that every stored
    credential opens, and the same whole-snapshot validation, so a
    stored half that fails that read is refused here under the status it
    would be refused under there.

    That is the whole of the equivalence, and it is worth being exact
    about which direction it runs in, since the other one is tempting
    and false. A stored half this refuses is a stored half a reload
    refuses, because they fail in the same function. A stored half this
    answers is not a reload that would apply: the reload has a second
    phase this has no part of, which builds a manager per referenced
    entry and refuses on an environment reference nothing sets, a
    credential a transport cannot use, or an entry `server.local_only`
    forbids. None of those is a comparison question, and none of them is
    answered here.

    One world, and this is the whole of how. The read is blocking (it
    takes the database's write lock and waits out its busy timeout), so
    it runs in a worker thread, and that await is itself the window: a
    reload can install a new world while it is in flight, leaving a
    stored generation compared against a runtime that never held it. The
    holder's mark is therefore taken on this loop before the read and
    read again after it, and the comparison runs only if it was settled
    both times and did not move, with no await of its own between that
    check and the answer. An apply changes serving state more than once,
    so the mark reads as nothing at all while one is running, and a
    sample that has no number is as good a reason to read again as two
    that disagree. A mark that keeps moving refuses with the retryable
    409 rather than composing a mixture.

    A refused read keeps its type and loses its words. The type is what
    the API turns into a status and is the store's own; the sentence is
    replaced by one of the three fixed ones above, because the store
    composes its sentences over the state it refused and this read's
    answers carry names and labels and nothing else. A failure with no
    type at all is a bug and is left alone: the API's last-resort
    middleware answers it as a sanitized 500 and records the exception's
    class, which is more than any sentence here could say.
    """

    async def stored_half() -> Loaded:
        """The stored configuration, read off this loop and refused in
        this route's words rather than the store's.

        Built in the handler and raised after it, which is this
        codebase's rule and load bearing here: raised inside one, the
        replacement would carry the original as its context, and the
        original is what holds the stored bytes. Neither `__cause__` nor
        `__context__` reaches the caller, and the caller is a response
        body.
        """
        refusal: ConfigError | None = None
        try:
            return await asyncio.to_thread(read)
        except DatabaseBusyError:
            refusal = DatabaseBusyError(_DIFF_DATABASE_BUSY)
        except StorageError:
            refusal = StorageError(_DIFF_UNREADABLE)
        except ConfigError:
            refusal = ConfigError(_DIFF_REFUSED)
        raise refusal

    async def diff() -> ConfigDiff:
        for _ in range(DIFF_LOADS):
            mark = generations.mark
            stored = await stored_half()
            # `is not None` and not equality alone: two unstable samples
            # are two moments inside an apply rather than one world that
            # held still, and equality would call them the same.
            if mark is not None and generations.mark == mark:
                return config_diff(
                    generations.current(),
                    stored,
                    servers.pending_against(stored.config, stored.secrets),
                )
        raise RunningConfigMovedError(CONFIG_MOVED)

    return diff


# What the identity read is told when onboarding is off, in the one
# place a caller of `onboarding_url` has to supply it. Never reached
# from here, because the branch below never asks for a URL a
# configuration does not serve; supplied anyway rather than left as a
# placeholder, since a sentence nobody wrote is a sentence nobody can be
# held to if the branch above it ever moves.
_NO_SHORT_URL_TO_SERVE = "Turn onboarding on for a URL short enough to type."


def _runtime_identity(server: ServerConfig) -> RuntimeInfo:
    """Which deployment this is, as the configuration API answers it.

    Resolved here because both halves of it are this root's to know: the
    build comes from `build_info`, which is a fact of the process, and
    the onboarding URL comes from `onboarding.origin`, which derives it
    from the origin this deployment names itself by and the key the OTA
    alias is mounted behind. There is one derivation of that URL and
    this is not a second one: `vinga-server config ota-url` reaches the
    same function from the file half alone, which is what keeps a
    deployment named identically wherever it is named.

    Once, at composition, rather than per request. Nothing in the answer
    moves while the process runs: a reload replaces the domain half and
    never the server section, which is where the origin, the path and
    the key all come from.

    Onboarding off is answered as nulls and a flag rather than as a
    refusal, because it is a state a deployment is legitimately in: what
    a device is configured at then is `server.ota_path`, which is that
    deployment's secret and is not a substitute this read could offer.
    The other refusal `onboarding_url` can raise, device auth on with no
    secret anywhere, is a state this server cannot be in: `create_app`
    and the composition above both refuse the boot for it.
    """
    if not server.onboarding.enabled:
        return RuntimeInfo(
            version=__version__,
            revision=revision(),
            onboarding_enabled=False,
            onboarding_url=None,
            onboarding_provenance=None,
        )
    url, origin = onboarding.onboarding_url(server, _NO_SHORT_URL_TO_SERVE)
    return RuntimeInfo(
        version=__version__,
        revision=revision(),
        onboarding_enabled=True,
        onboarding_url=url,
        onboarding_provenance=origin.provenance,
    )


def _prompt_preview(
    generations: Generations, servers: McpServers, memory: MemoryStore
) -> Callable[[str], Awaitable[prompt.Assembled | None]]:
    """What the configuration API's prompt read calls.

    Closed over here for the reason the reload is: the three things an
    assembly needs (the holder whose generation is what this server
    serves, the MCP registry that is running, and the memory store this
    server writes) are known at the composition root and nowhere else,
    and the API application must not learn what a prompt is made of.

    The configuration is read from the holder per request, exactly as an
    activation reads it, which is what keeps this a preview of what a
    session opening now would be sent rather than of what one opening at
    boot would have been.

    An agent this server is not serving answers None rather than
    raising, so the route decides what a missing one means. The guidance is read
    on the loop that owns the managers, before any await; the memory
    read is a database round trip and goes to a worker thread, which is
    what keeps an inspection request off the loop every live
    conversation is on.

    Agent-keyed, and only that. What this renders is what a fresh
    session with no device and no thread behind it would be sent, which
    is the read itself said with no device and no conversation: the
    agent's own block, as bounded here as it is in a reply, and no other
    scope. A preview that invented a device to show its notes, or a
    conversation to show its ledger, would be a second prompt assembler
    pretending to be the first. The route's own description says so where
    a caller reads it.

    Agent-keyed also means the agent's own `memory` section applies: one
    that may not remember is previewed with no block and costs no read,
    which is the prompt a reply of that agent's carries.
    """

    async def assemble(agent: str) -> prompt.Assembled | None:
        config = generations.current().config
        if agent not in config.agents:
            return None
        half = prompt.know_how(
            config.prompt_for_agent(agent),
            config.fragments_for_agent(agent),
            servers.guidance_for_agent(agent),
        )
        # An agent whose memory section is off is sent no block, so this
        # shows none and reads nothing, exactly as a reply of that
        # agent's does. A preview that showed the facts anyway would be
        # showing a prompt this deployment does not send.
        if not config.memory_for_agent(agent).enabled:
            return half
        scopes = await asyncio.to_thread(memory.read_for_prompt, agent, None, None)
        return prompt.with_scopes(half, scopes)

    return assemble


def create_app(
    config: Config | None = None,
    secrets: SecretStore | None = None,
    on_started: Callable[[], None] | None = None,
    from_store: bool = False,
) -> FastAPI:
    """Describe the ASGI app: its routes, its gate, and what its lifespan
    will build. Without a config the whole boot configuration is read here
    (the file named by VINGA_CONFIG plus the domain half from the
    database), which is what an external ASGI server gets; the CLI reads
    it itself (it also honours --config) and passes both halves in.

    `secrets` are the stored credentials the snapshot was loaded with,
    None for a configuration whose credentials are all environment
    references. They reach exactly the two places a credential is
    materialized: building a provider, and connecting an MCP server.

    `on_started` is called once the lifespan has built everything and
    before the first request, and is how the CLI says out loud where to
    point a device. None for an app built by a test lane or an external
    ASGI runner, which has no operator reading its startup output.

    `from_store` says whether the configuration handed in was read from
    the database, which is true of both production entry points and
    false of an embedded caller composing one in Python. It decides
    whether device bindings resolve live from the database or from the
    snapshot, authoritatively: a server that was handed its world is a
    server whose world no store describes, and reading a store that
    describes some other deployment would answer the right question
    about the wrong server. It is stated by the caller rather than
    probed here, because it is a fact about how this application was
    composed. Reading it here when the config is None is not an
    exception to that: `create_app()` with nothing composes from the
    store itself, two lines below.

    Nothing here opens a database, starts a thread or loads a model: an
    app that is described and never served holds nothing (#142). What it
    does do is refuse a deployment that cannot work at all, which stays
    here because a configuration that is missing a secret should be
    refused by whatever built the app, however it was launched."""
    # The floor under the libraries that narrate somebody else's bytes
    # (#124), applied before anything below can reach a database or a
    # socket. Here for the same reason the line below is here: a
    # production process may serve this app under an external ASGI
    # runner and never reach `main()`, and `uvicorn --log-level debug`
    # would then leave uvicorn's own logger tracing request lines,
    # request headers and websocket frame payloads onto the retained
    # log. Without a level, because the server's own is in the
    # configuration this function is about to read; `logs.configure`
    # applies it again with that level once it has it, and the call is
    # idempotent.
    logs.quiet_vendor_libraries()
    # No interactive docs, no schema. A device needs two paths and a
    # healthcheck needs a third; publishing an API description of them to
    # anyone who asks is surface with no reader, and the security default
    # is that nothing beyond what a device needs is exposed.
    app = FastAPI(
        title="vinga-server",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    if config is None:
        booted = load_boot_config()
        config, secrets = booted.config, booted.secrets
        # Composed from the store, by this very line.
        from_store = True
    # Read here and thrown away: this is the refusal, not the issuer.
    # Enabled authentication with no secret in the environment is a
    # deployment that would come up serving every device that connects,
    # and it must be refused by whatever built the app rather than
    # minutes later inside a lifespan. The issuer itself is built where
    # everything else is, and belongs to the composition that holds it.
    build_device_auth(config)
    # The configuration API's token, resolved here rather than at the
    # call below and for the reason it always was: the API is always
    # mounted, so a deployment that forgot the variable must be refused
    # before anything else is built rather than serve an admin surface
    # its own operator cannot reach. Held in a local, passed straight
    # into the gate, and kept nowhere else, least of all on app.state or
    # the seed below, and never logged.
    token = api_token(config)
    # The API as a shell: its routes, and the gate armed with the token.
    # What its requests resolve out of the server around it is attached
    # by the lifespan, because those are live objects and this function
    # builds none. Until then it answers as an application built without
    # a server around it, which is what `build_api` has always meant by
    # its own defaults.
    api = build_api(token, config.server.database)

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
        app.include_router(ota.build_alias_router(key))
    app.include_router(ws.router)

    # Mounted last, so the device-facing routes are what this app is
    # and the configuration API is one gated object hanging off it. It
    # is control plane: it accepts inbound requests and sends nothing
    # anywhere, so server.local_only has nothing to say about it.
    mount_api(app, api)

    # What the lifespan builds from. The mounted application is on it
    # because Starlette runs no lifespan for a mounted app, so the parent
    # is what installs its request-time pieces and has to be handed it;
    # the token is not, and is now held only by the gate it was passed
    # to.
    app.state.seed = _CompositionSeed(
        config=config,
        secrets=secrets,
        api=api,
        on_started=on_started,
        from_store=from_store,
    )

    return app


def __getattr__(name: str) -> FastAPI:
    """Build the module-level `app` on first access, so that `uvicorn
    vinga_server.app:app` still works while importing create_app (as the CLI
    does) neither loads the config twice nor turns a config error into an
    import traceback."""
    if name == "app":
        instance = create_app()
        globals()["app"] = instance
        return instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
