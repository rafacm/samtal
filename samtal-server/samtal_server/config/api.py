"""The configuration REST API: one sub-application, mounted at /api.

A second FastAPI instance rather than a router on the device-facing
app, for three reasons. The committed OpenAPI document is then exactly
the admin surface, with the device endpoints structurally absent from
it, while the main app keeps publishing no schema at all. The token
gate is a property of the mount instead of something each new route
decorator could forget. And `local_only` stays one sentence in
`app.py`: this is control plane, it accepts inbound requests and sends
nothing anywhere, so it needs no egress declaration.

Nothing here decides anything about the configuration. The repository
(`store.py`) validates fragments, checks references, decides what
exists and keeps secrets write-only; `views.py` decides what a read may
show. A handler that restated any of that would be the bug. What this
module owns is transport: the token, the path an entity is addressed
by, the status code a refusal maps to, and the shape of a body.

The shapes themselves are one module below, in `responses.py`, which
imports pydantic and nothing else. What a read answers is knowledge the
CLI needs too, and the CLI must not import FastAPI to get it.

One namespace here is not configuration at all. The conversation
store's reads (`/conversations`) are registered from
`conversations/api.py`, where their route functions and their response
models live, because what they answer is that store's business and not
this one's. They are registered on this application so that the gate,
the sanitized handlers and the committed document cover them by
construction, which is the whole reason a route belongs on this mount
rather than on an application of its own.

The gate is ASGI middleware, not a dependency, because a dependency
only runs for a matched route: an unmatched path inside /api would
answer 404 to an unauthenticated caller, which leaks which routes exist
and breaks "the namespace is gated" as a property. Enforcement being
middleware is also why the bearer scheme is stated explicitly in the
document below rather than falling out of a security dependency.

Nothing logs the token, a request body, or an Authorization header.
"""

import hmac
import os
from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from inspect import Parameter, Signature
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Body, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Connection
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from samtal_server.config import entities, views
from samtal_server.config.docgen import API_OPTIONS_NOTE
from samtal_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    DeviceAlreadyBoundError,
    ReloadInProgressError,
    StorageError,
    UnknownEntityError,
)
from samtal_server.config.models import (
    API_MOUNT_PATH,
    Config,
)
from samtal_server.config.responses import (
    Acknowledgement,
    AssembledPrompt,
    ConfigDocument,
    DefaultAgent,
    DefaultAgentName,
    DeviceBinding,
    Envelope,
    McpReloader,
    McpReloadResult,
    McpServerStatus,
    McpStatusSource,
    PendingDevice,
    Problem,
    PromptBlock,
    SecretSlot,
    SecretValue,
    StoredSecretLocation,
)
from samtal_server.config.secrets import SecretLocation, load_keys
from samtal_server.config.store import ConfigStore
from samtal_server.config.writes import (
    BINDING_NOTICE,
    CLEARED_DEFAULT_AGENT,
    RESTART_NOTICE,
    binding_notice,
    bound_device,
    cleared_secret,
    deleted_device,
    wrote_default_agent,
    wrote_secret,
)
from samtal_server.conversations import api as conversations
from samtal_server.db import open_database
from samtal_server.events import ServerEvents

if TYPE_CHECKING:
    # For the reader, never at runtime. The pending table is a device
    # concern, and its module imports the OTA endpoint, which imports
    # the websocket session and everything a conversation needs; this
    # application is also what `config openapi` renders a document from,
    # with no server anywhere, and it must not have to load any of that
    # to do it. The table arrives as an argument and is used through the
    # small surface named here.
    from samtal_server.onboarding import PendingDevice as PendingRecord
    from samtal_server.onboarding import PendingDevices

    # The MCP registry was named here too, for the same reason and with
    # more force: it imports the SDK's clients and this project's
    # provider layer, none of which rendering a document has any
    # business loading. It is not named anywhere any more. What the
    # routes are handed is `McpStatusSource` and `McpReloader` from
    # `responses.py`, which say what this application asks of a running
    # server out of typing and the response models, so the annotations
    # resolve at import and the constraint holds by construction rather
    # than by a forward reference nobody may resolve.

events = ServerEvents(__name__)

# Where the sub-application is mounted on the server's own port. A
# mounted application renders its internal paths (/config, not
# /api/config), so this is also what the document carries as its
# server URL. Single-sourced on the models, where `server.ota_path`'s
# validator needs it to keep the OTA endpoint out of this namespace.
MOUNT_PATH = API_MOUNT_PATH

# The API contract's version, fixed and deliberately not the package
# version: a release does not change the contract, and a committed
# document that rewrote itself on every version bump would make the
# drift check cry wolf.
API_VERSION = "1"

API_TITLE = "samtal-server configuration API"

API_DESCRIPTION = (
    "The domain half of one samtal-server deployment's configuration: providers, "
    "MCP servers, agents, devices, and the credentials they use. Every request "
    "carries a bearer token, whose value is the environment variable "
    "`server.api.secret_env` names.\n\n"
    "Configuration is a boot-time snapshot, so a successful write applies at the "
    "next server start rather than immediately. Device bindings are the one "
    "exception: a running server reads them, and the default agent, as a device "
    "asks for them, so binding or unbinding a device applies at that device's "
    "next OTA check or connection. The exception ends where the agent does, "
    "since a server builds an agent's providers at boot: a binding naming an "
    "agent this server has not loaded waits for the restart that loads it. Every "
    "write says which of the two happened, in its `notice`.\n\n"
    "A device with no binding and no default agent to cover it is answered at its "
    "configuration check with a six-digit activation code, which it shows on its "
    "screen and speaks. `/devices/pending` lists the devices showing one, keyed by "
    "the code, and posting to `/devices/pending/{code}` binds the device showing it. "
    "That listing is the running server's own state rather than stored "
    "configuration: a restart forgets it, and the devices come back with fresh "
    "codes.\n\n"
    "The `/runtime` namespace is the running server's own state as well, and is "
    "kept apart from the entity namespaces because an entity may legally be named "
    "after any word a route might want. `/runtime/mcp-servers` says what each "
    "configured MCP server is doing right now: connected or down, since when, what "
    "it published, and which agents may reach it. Nothing there is read from the "
    "database, so it cannot disagree with what is running.\n\n"
    "`GET /runtime/agents/{name}/prompt` is the other read there: the system prompt "
    "a session opening now as that agent would be sent, block by block, with the "
    "provenance and the character count of each and the total. It is assembled from "
    "the loaded agents, the running MCP slice and the memory store rather than from "
    "the database, so it cannot disagree with what a session would get, and it is a "
    "preview of a new session rather than a readback of a running one: a conversation "
    "already in progress holds the half it assembled at its own activation.\n\n"
    "`POST /runtime/mcp-servers/reload` is the second exception to the boot-time "
    "snapshot, and unlike device bindings it is asked for rather than noticed. It "
    "re-reads the `mcp_servers` entries, the secrets stored on them and the agents' "
    "`mcp` grant lists, and applies them to the running server: entries are started, "
    "restarted, stopped or left alone, and no conversation is dropped. When a live "
    "conversation meets the result depends on which half of an entry moved. The tools "
    "it may reach are snapshotted per reply, so a started, restarted or stopped entry "
    "is picked up on its next utterance. An entry's `instructions` is prompt text, and "
    "prompt text is assembled at an activation and cached for it, so a rewrite reaches "
    "a conversation at its next activation, a new session or an agent switch, and "
    "never a reply of one already running; `GET /runtime/agents/{name}/prompt` "
    "previews what a session opening now would be sent. Everything else about an agent "
    "still waits for a restart, which is why writes to those keep saying so.\n\n"
    "The `/conversations` namespace is not stored configuration either: it reads the "
    "conversation store, the record of what was said, which "
    "`server.conversations.enabled` switches on. Three reads: the session list "
    "newest first, one session whole, and one session's turns oldest first with the "
    "calls each turn made nested under it. The two listings are cursor-paginated on "
    "monotonic row ids that are never reused, and the session read is singular and "
    "takes neither `limit` nor `cursor`. A deployment that never recorded answers 404; one that "
    "recorded and has since switched recording off still serves what it recorded, "
    "because switching recording off stops the writer and not the reader. Content "
    "columns come back as they were stored, which is null where text storage was "
    "off, and every session says which way its switches were set.\n\n"
    f"{API_OPTIONS_NOTE}"
)

# The name the security scheme is registered under. Referenced by the
# document-level requirement, so both come from one string.
BEARER_SCHEME = "bearerToken"


class NoRuntimeError(ConfigError):
    """A runtime action was asked of an application with no server
    around it, which has nothing to act on.

    A transport concern like the one below, and here for the same
    reason: what is missing is not stored configuration but the running
    thing this application would have been mounted on. The reads in the
    `/runtime` namespace answer emptily instead, because an empty
    listing is a true description of no runtime; an action has no
    equivalent honest answer.
    """


class ClaimInFlightError(ConfigError):
    """Another request is in the middle of claiming the same activation
    code. Nothing was changed, and the same call may be retried.

    A transport concern rather than a repository one, which is why it
    lives here: the pending table is runtime state of the server this
    application is mounted on, and this is the one refusal that comes
    out of it rather than out of the database.
    """


# What a refusal maps to. Plain ConfigError is the default: a fragment
# whose shape is wrong, a reference that would be left unresolved, a
# slot that is not a credential slot, a stage that is not a stage.
REFUSAL_STATUS: dict[type[ConfigError], int] = {
    UnknownEntityError: 404,
    # The same status for the same reason: what the request addressed,
    # a device waiting to be claimed under this code, is not there.
    DeviceAlreadyBoundError: 404,
    DatabaseBusyError: 409,
    ClaimInFlightError: 409,
    # The third thing that is held rather than wrong: one reload of the
    # MCP servers runs at a time, and the second is retryable exactly as
    # a contended write is.
    ReloadInProgressError: 409,
    StorageError: 500,
    NoRuntimeError: 503,
    ConfigError: 422,
}

# Said to a caller with no token or the wrong one, and identical for
# both: only an authenticated caller learns anything about this API,
# including whether a token was close.
UNAUTHORIZED = (
    "this API requires a bearer token: send it as the Authorization header, "
    "`Authorization: Bearer <token>`, where the token is the value of the "
    "environment variable server.api.secret_env names"
)

# The request never gets quoted back, here least of all: a body that
# fails to parse may be a fragment carrying a pasted credential.
MALFORMED_REQUEST = (
    "the request could not be read in the shape this endpoint expects; send a "
    "JSON object body, and see the committed OpenAPI document for the shape. "
    "The body is never quoted back"
)

# What a caller is told when something in here failed rather than
# something in the request. The log records that it happened and what
# kind of failure it was, and deliberately no more than that.
UNEXPECTED = "the server failed to handle this request; the failure is recorded in its log"

# The entity models the document carries the schemas of, which is one
# per commanded kind, in the order the registry lists them. FastAPI
# collects the models its own routes declare, and the write routes
# declare none: a fragment is received as a raw object and validated in
# the repository, because FastAPI's own validation echoes the input it
# rejected and a fragment can carry a pasted credential. So they are
# injected below instead, which is also what a client that has read an
# envelope needs in order to write one back.
ENTITY_MODELS: tuple[type[BaseModel], ...] = tuple(
    descriptor.model for descriptor in entities.ENTITIES
)

# The three bodies that are arguments rather than fragments, as the
# document describes them. The models are documentation and nothing
# else: they are injected into `components` beside the entity models and
# named by the routes' `openapi_extra`, and they are deliberately not
# declared as body types, for the reason the entity models are not
# either. What enforces them at runtime is the exact-shape parser
# further down, which describes the expectation and never echoes what it
# refused.
REQUEST_MODELS: tuple[type[BaseModel], ...] = (DeviceBinding, DefaultAgentName, SecretValue)

# What each argument-shaped body must be, said as an expectation rather
# than as a complaint about what arrived. The body is never quoted back,
# and for the secret write that is not a nicety: the rejected value is a
# credential often enough that echoing it would make the refusal the
# leak.
DEVICE_BODY = (
    'the body has to be a JSON object with exactly one key, "agents", holding an '
    "array of agent names as strings. Nothing sent is quoted back"
)

DEFAULT_AGENT_BODY = (
    'the body has to be a JSON object with exactly one key, "name", holding the '
    "agent's name as a string. Nothing sent is quoted back"
)

SECRET_BODY = (
    'the body has to be a JSON object with exactly one key, "secret", holding the '
    "credential as a non-empty string. Nothing sent is quoted back, which on this "
    "endpoint is the point"
)

# The two refusals a claim by code can meet. Neither quotes the code
# back: it is what arrived in the path, and what is worth saying about
# it is what the operator should read instead.
UNKNOWN_CODE = (
    "no device is waiting with that activation code. A code lasts ten minutes and is "
    "retired the moment it is claimed, and a device that has been waiting longer is "
    "already showing a fresh one: read the code currently on the device's screen and "
    "use that. `samtal-server config pending` lists the codes this server is showing "
    "right now."
)

CODE_IN_FLIGHT = (
    "that activation code is being claimed by another request right now. Nothing was "
    "changed; run the command again in a moment, when the code will either have been "
    "bound by that request or be free again."
)

# And what a claim refused by the repository says instead of the
# repository's own sentence, which names the agent it could not resolve.
# This is the one route where an agent name is typed beside an
# activation code rather than read out of stored configuration, so it is
# the one route where a mistake puts something else there.
CLAIM_REFUSED = (
    "the device showing that code could not be bound: the request's agents name at "
    "least one agent this deployment does not have. Nothing was changed and the code "
    "is still claimable. What was sent is not quoted back; run "
    "`samtal-server config list` to see the agents that exist."
)

# How the document describes each refusal a route can answer with. The
# sentence a caller actually receives is the repository's own; these say
# what the status means.
PROBLEM_DESCRIPTIONS: dict[int, str] = {
    401: "The request carried no bearer token, or not the one this server was given.",
    404: "Nothing of that identity exists.",
    409: (
        "Something this request needs is held by another one: the configuration "
        "database's write lock, the activation code a concurrent claim is already "
        "binding, or a reload of the MCP servers that is already running. Nothing was "
        "changed and the request can be retried."
    ),
    422: (
        "The request names something that cannot be addressed, such as a stage that is "
        "not a provider stage or a MAC address that is not one."
    ),
    500: (
        "The stored configuration cannot be read, or the request failed for a reason "
        "that is not the caller's. The details are in the server's log."
    ),
    503: (
        "This application has no running server around it, so there is nothing for a "
        "runtime action to act on. A deployment reaches the API on its server's own "
        "port; an application built without one serves the reads in the /runtime "
        "namespace emptily and refuses the actions."
    ),
}

# What a prompt read answers for an agent this server did not load. The
# name is not quoted back: it arrived in the path, and what is worth
# saying about it is where to look instead.
UNLOADED_AGENT = (
    "this server has not loaded an agent of that name. An agent's providers are built "
    "at boot, so one written since this server started is served by the restart that "
    "builds it, and one that never existed is a name nothing answers to. "
    "`samtal-server config list` shows the agents that are stored."
)

UNLOADED_AGENT_DESCRIPTION = (
    "No agent of that name was loaded when this server started. An agent written "
    "since then waits for the restart that builds its providers."
)

# Declared on the routes whose only 422 is the framework's own, so the
# document carries the sanitized shape this API actually answers with
# rather than FastAPI's default one, which lists the input it rejected
# per error. Nothing here validates a body, so this is the request that
# could not be read at all.
MALFORMED_REQUEST_DESCRIPTION = (
    "The request could not be read in the shape this endpoint expects. The refusal is "
    "the sanitized `Problem` body every other refusal uses, and nothing sent is quoted "
    "back."
)

# The shared 503 sentence is about actions, and says the reads in this
# namespace answer emptily. That is true of the MCP status read and
# false of this one: there is no honest empty prompt.
NO_RUNTIME_PROMPT_DESCRIPTION = (
    "This application has no running server around it, so there is no loaded agent, no "
    "running MCP slice and no memory store to assemble a prompt from. Unlike the MCP "
    "status read beside it, there is no honest empty answer: an empty block list would "
    "say a session opening now is sent nothing."
)

# The reload takes no body and addresses nothing, so the shared sentence
# for 422 (a stage that is not a stage, a MAC that is not one) cannot be
# what one of its own means. What it means instead is the whole of the
# guarantee the endpoint makes about a refusal.
RELOAD_REFUSED_DESCRIPTION = (
    "The stored configuration was refused: it does not compose into a valid snapshot, "
    "or a server it names could not be built (an environment reference nothing sets, a "
    "stored credential that will not decrypt, an entry `server.local_only` forbids). "
    "Nothing was stopped, started or swapped, and the running servers are exactly as "
    "they were."
)


@dataclass
class ApiRuntime:
    """Everything a request to this application resolves out of the
    server around it, as one typed object.

    The sub-application carries exactly this, under `state.api_runtime`,
    and every dependency below reads a field of it. It used to be seven
    loose attributes on the same state bag, which meant the reader of any
    one of them had to know what the composition root had happened to
    attach and what its type was; a dataclass says both in one place, and
    is what the whole-server composition carries as its `api` field
    (#142).

    `store` and `conversations` are the two per-request database handles,
    callables rather than open engines for the reason their factories
    document. The other five are the live objects the server shares with
    this application, or the honest empties an application built without
    a server around it gets.
    """

    store: Callable[[], Iterator[ConfigStore]]
    conversations: Callable[[], Iterator[Connection]]
    loaded_agents: frozenset[str]
    # Quoted, and the field never annotated with anything this module
    # imports at runtime: the pending table is a device concern whose
    # module pulls in the whole conversation stack, and `document()`
    # renders this application with none of it loaded.
    pending: "PendingDevices"
    mcp_servers: McpStatusSource | None
    mcp_reload: McpReloader | None
    agent_prompt: Callable[[str], Awaitable[Any]] | None


def build_api(
    token: str,
    database_dir: Path,
    loaded_agents: Collection[str] = (),
    pending: "PendingDevices | None" = None,
    mcp_servers: McpStatusSource | None = None,
    mcp_reload: McpReloader | None = None,
    agent_prompt: Callable[[str], Awaitable[Any]] | None = None,
) -> FastAPI:
    """The sub-application the server mounts: the routes, gated.

    `token` is compared against every request's bearer token, and is
    resolved once at app build by `api_token` below rather than read per
    request, so a deployment that forgot the variable is refused at boot
    instead of at the first call.

    `loaded_agents` are the agents the server around this application
    built providers for at boot, which is what a device write's
    acknowledgement needs in order to say whether the write is live: a
    binding to an agent nothing loaded waits for a restart. Empty is the
    honest answer for an application built without a server around it,
    and answers every write with the restart sentence.

    `pending` is the serving app's table of devices showing activation
    codes, shared rather than copied: the OTA endpoint writes it and the
    claim route reads it, and they are the same object or the ceremony
    does not work. An application built without a server gets a table of
    its own, which is empty and stays empty, so every code is unknown.

    `mcp_servers` are that server's live MCP managers, shared for the
    same reason: the status read reports what is running, and a copy
    would report what was running once. None is the honest answer for an
    application built without a server, and the read answers with an
    empty object.

    `mcp_reload` applies a re-read of the stored configuration to those
    managers. A callable rather than the pieces it needs, because what
    it closes over is the composition root's business: the configuration
    this process booted on, whose server section the re-read composes
    onto, and the registry that owns where the blocking half of it runs.
    None is the honest answer for an application without a server, and
    the route refuses with 503 rather than pretending to have applied
    something.

    `agent_prompt` assembles the prompt a session opening now as one
    agent would be sent, and answers None for an agent this server did
    not load. A callable for the reason the reload is one: what it
    closes over is the composition root's business again (the loaded
    configuration, the MCP registry and the memory store), and this
    application must not learn what a prompt is made of. None is the
    honest answer without a server, and the route answers 503.
    """
    api = _application()
    # Attached rather than closed over: the read and write routes take
    # the pieces of it with Depends(...), and milestone 1 had none of
    # them yet. One object rather than one attribute each, so what a
    # route resolves is a field of a declared type.
    api.state.api_runtime = build_api_runtime(
        database_dir, loaded_agents, pending, mcp_servers, mcp_reload, agent_prompt
    )
    # Added last is outermost, so a failure inside the gate itself
    # answers as sanitized as one inside a handler.
    api.add_middleware(_BearerGate, token=token)
    api.add_middleware(_SanitizedErrors)
    return api


def build_api_runtime(
    database_dir: Path,
    loaded_agents: Collection[str] = (),
    pending: "PendingDevices | None" = None,
    mcp_servers: McpStatusSource | None = None,
    mcp_reload: McpReloader | None = None,
    agent_prompt: Callable[[str], Awaitable[Any]] | None = None,
) -> ApiRuntime:
    """What a request to this application resolves out of the server
    around it, assembled.

    Separate from `build_api` because the two happen at different times
    on the serving path: the application is described when the server is
    described, and the live objects it shares only exist once the
    lifespan has built them (#142). The server's composition root calls
    this and installs the result on the mounted application; `build_api`
    calls it with whatever it was given, which for an application built
    without a server around it is the honest empties its own docstring
    describes.
    """
    return ApiRuntime(
        store=store_dependency(database_dir),
        # The same directory, read for the other database in it. The
        # conversation reads need no more runtime fact than this: whether
        # there is a store to read is whether the file is there, which
        # `enabled` decides at boot and cannot be asked again here, since
        # a deployment that has switched recording off still serves what
        # it recorded.
        conversations=conversations.reader(database_dir),
        loaded_agents=frozenset(loaded_agents),
        pending=pending if pending is not None else _empty_pending(),
        mcp_servers=mcp_servers,
        mcp_reload=mcp_reload,
        agent_prompt=agent_prompt,
    )


def _empty_pending() -> "PendingDevices":
    """A table for an application built without a server around it.

    Imported here rather than at module scope, which is the point of the
    seam: `document()` never calls this, so rendering the contract still
    loads nothing of the device stack.
    """
    from samtal_server.onboarding import PendingDevices

    return PendingDevices()


def document() -> dict[str, Any]:
    """The OpenAPI document of the API as it is served.

    Rendered from the same application factory `build_api` uses, minus
    the token gate and the store dependency, neither of which a document
    describes: the bearer scheme is stated explicitly below, and the
    database is not part of the contract.
    """
    return _application().openapi()


def api_token(config: Config) -> str:
    """The API's bearer token, from the environment.

    A missing one is a boot failure and not a warning, the shape device
    auth already has: an admin surface is not something to serve open
    while somebody notices. The message carries the fix, because this is
    the error a first deployment after the upgrade meets.
    """
    name = config.server.api.secret_env
    token = os.environ.get(name, "").strip()
    if not token:
        raise ConfigError(
            f"the configuration API is mounted at {MOUNT_PATH} but {name} is not set.\n"
            f"Generate a token and put it in the environment:\n"
            f"  {name}=$(openssl rand -hex 32)\n"
            f"It is the bearer token every request to {MOUNT_PATH} must carry, and it "
            f"grants everything the API can do, so keep it to a loopback connection "
            f"or TLS."
        )
    return token


def store_dependency(directory: Path) -> Callable[[], Iterator[ConfigStore]]:
    """Per-request access to the repository: open the database, yield
    the store, dispose the engine.

    The CLI's `_store` in dependency form, and deliberately the same
    lifetime rather than one engine held for the app's life: `boot.py`'s
    contract is that nothing after boot reads the database, and an
    engine opened eagerly would also make every `create_app(...)` in the
    test suites open a database again. The cost is one Alembic
    up-to-dateness check per request, which an admin surface with one
    operator and one front-end can pay.
    """

    def store() -> Iterator[ConfigStore]:
        engine = open_database(directory)
        try:
            yield ConfigStore(engine, load_keys())
        finally:
            engine.dispose()

    return store


def _store(request: Request) -> Iterator[ConfigStore]:
    """The repository, for the length of one request.

    Taken from the application rather than closed over by the routes, so
    that the document can be rendered from an application built without
    a database directory: `build_api` attaches the dependency and
    `document()` never resolves it.
    """
    runtime: ApiRuntime = request.app.state.api_runtime
    yield from runtime.store()


StoreDep = Annotated[ConfigStore, Depends(_store)]


def _loaded_agents(request: Request) -> frozenset[str]:
    """Which agents the server around this application loaded at boot.

    Taken from the application for the reason the store is: the document
    is rendered from an application built without a server, and nothing
    a route declares may depend on there being one.
    """
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.loaded_agents


LoadedAgentsDep = Annotated[frozenset[str], Depends(_loaded_agents)]


def _pending(request: Request) -> "PendingDevices":
    """The devices waiting to be claimed, from the server this
    application is mounted on. Taken from the application for the reason
    the store is."""
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.pending


# Annotated `Any` rather than the real type on purpose: FastAPI resolves
# a route's annotations at import, so a forward reference to a class
# this module deliberately does not import at runtime would fail to
# resolve. The dependency function above carries the honest type.
PendingDep = Annotated[Any, Depends(_pending)]


def _mcp_servers(request: Request) -> McpStatusSource | None:
    """The running server's MCP managers, or None for an application
    built without a server around it. Taken from the application for the
    reason the store is."""
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.mcp_servers


# `Any` stood here for the reason PendingDep's does: FastAPI resolves a
# route's annotations at import, and the registry's own type is a name
# this module deliberately does not import at runtime. What replaces it
# is not that type but the surface a route actually reaches it through,
# declared in `responses.py` out of typing and the response models, so
# the annotation resolves at import with the MCP SDK nowhere near it.
McpServersDep = Annotated[McpStatusSource | None, Depends(_mcp_servers)]


def _mcp_reload(request: Request) -> McpReloader | None:
    """What applies a re-read of the stored configuration to the running
    MCP managers, or None for an application built without a server.
    Taken from the application for the reason the store is."""
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.mcp_reload


McpReloadDep = Annotated[McpReloader | None, Depends(_mcp_reload)]


def _agent_prompt(request: Request) -> Callable[[str], Awaitable[Any]] | None:
    """What assembles one agent's prompt from the running server, or
    None for an application built without one. Taken from the
    application for the reason the store is."""
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.agent_prompt


AgentPromptDep = Annotated[Any, Depends(_agent_prompt)]


def _pending_view(device: "PendingRecord") -> dict[str, Any]:
    """One waiting device as the listing shows it. The code is the key
    it is filed under and is deliberately not repeated inside."""
    return {
        "mac": device.mac,
        "client_id": device.client_id,
        "board": device.board,
        "firmware": device.firmware,
        "first_seen": _instant(device.first_seen),
        "last_seen": _instant(device.last_seen),
        "expires_at": _instant(device.expires_at),
    }


def _instant(when: float) -> str:
    """One of the table's timestamps, as a person reads it. UTC, because
    a listing compared against a server's log is compared against a
    server's clock."""
    return datetime.fromtimestamp(when, UTC).isoformat()


def _problems(
    *statuses: int, instead: Mapping[int, str] | None = None
) -> dict[int | str, dict[str, Any]]:
    """The refusals a route can answer with, as the document describes
    them. Declaring 422 here also replaces FastAPI's own
    validation-error response, which describes a body shape the
    sanitized handler never sends.

    `instead` replaces the shared description on one route, for a route
    the shared one cannot be true of. The descriptions say what a status
    means here, so a route whose 422 can only mean something else has to
    say that rather than inherit a sentence about addressing.
    """
    described = {**PROBLEM_DESCRIPTIONS, **(instead or {})}
    return {
        status: {"model": Problem, "description": described[status]} for status in statuses
    }


# A body, exactly as it was sent, handed to the repository unread.
#
# Deliberately not the entity model as a body type. FastAPI's own
# validation echoes the input it rejected back in its 422 (`"input":
# ...` per error), and a fragment can carry a credential pasted where a
# variable name belongs, so declaring the model here would put that
# value in a response body. Received as an opaque object instead and
# validated in exactly one place, the repository, which is where the
# YAML file is validated too: one code path, one sanitized message
# shape, no second dialect.
RawBody = Annotated[Any, Body()]

# Which HTTP method each verb is. A collection and an entry are both
# read, a secret and the entity it hangs on are written and deleted the
# same way, so six verbs are three methods.
_METHOD: dict[entities.Verb, str] = {
    entities.READ_ALL: "GET",
    entities.READ_ONE: "GET",
    entities.WRITE: "PUT",
    entities.WRITE_SECRET: "PUT",
    entities.DELETE: "DELETE",
    entities.DELETE_SECRET: "DELETE",
}

# The verbs that read, and the verbs that write. The document lists
# every read before every write, because that is the order the two
# functions below register them in, and the committed bytes have that
# order in them.
READS: tuple[entities.Verb, ...] = (entities.READ_ALL, entities.READ_ONE)
WRITES: tuple[entities.Verb, ...] = (
    entities.WRITE,
    entities.DELETE,
    entities.WRITE_SECRET,
    entities.DELETE_SECRET,
)


def _entity_routes(api: FastAPI, verbs: Collection[entities.Verb]) -> None:
    """The routes of every commanded kind, built from what its
    descriptor says it has.

    A kind's routes used to be twenty-two hand-written handlers that
    differed from each other in four ways: the path, the repository
    method, the sentence, and the prose. Three of those four are the
    registry's now. The fourth is prose, and prose is what the committed
    OpenAPI document is made of, so it stays written down per endpoint
    rather than composed from the kind's name: a summary assembled out
    of `title` would read almost like the one that is committed, and
    almost is a drift check that fails.

    Registered in the order the registry lists them, kind by kind, which
    is the order the document has: the paths appear as they are first
    registered and the operations under a path in the order they were
    added to it.
    """
    for descriptor in entities.ENTITIES:
        for endpoint in descriptor.endpoints:
            if endpoint.verb in verbs:
                _install(api, descriptor, endpoint)


def _install(
    api: FastAPI, descriptor: entities.EntityDescriptor, endpoint: entities.Endpoint
) -> None:
    """One route, with everything the document carries about it said out
    loud: the operation's name, its description, what it answers and
    what it can refuse."""
    extra: dict[str, Any] = {}
    if endpoint.verb == entities.WRITE:
        extra["openapi_extra"] = _request_body(descriptor.model)
    elif endpoint.verb == entities.WRITE_SECRET:
        extra["openapi_extra"] = _request_body(SecretValue)
    api.add_api_route(
        _path(descriptor, endpoint),
        _handler(descriptor, endpoint),
        methods=[_METHOD[endpoint.verb]],
        response_model=endpoint.response,
        responses=_problems(*endpoint.statuses),
        **extra,
    )


def _path(descriptor: entities.EntityDescriptor, endpoint: entities.Endpoint) -> str:
    """Where the route lives: the kind's prefix, the parameters that
    address one entry under it, and for a credential the slot inside
    that entry. An identity rides in the path as one decoded segment,
    so a name carrying a space, a percent sign or a character outside
    ASCII is reached by percent-encoding it and nothing else."""
    path = descriptor.route
    if endpoint.verb != entities.READ_ALL:
        path += "".join(f"/{{{parameter}}}" for parameter in descriptor.addressing)
    if endpoint.verb in (entities.WRITE_SECRET, entities.DELETE_SECRET):
        path += "/secrets/{slot}"
    return path


def _handler(
    descriptor: entities.EntityDescriptor, endpoint: entities.Endpoint
) -> Callable[..., dict[str, Any]]:
    """The route function, built rather than written.

    FastAPI reads three things off a handler that the document then
    carries: its name, which becomes the operation id and the summary;
    its docstring, which becomes the description; and its signature,
    which becomes the parameter list in the order it declares them. All
    three are set here from the descriptor, because a generated function
    would otherwise contribute whatever it happened to be called.

    Plain `def`, like the handlers it replaces, so FastAPI runs it on
    the threadpool and the synchronous repository never blocks the event
    loop.
    """
    act = _act(descriptor, endpoint)

    def handler(**values: Any) -> dict[str, Any]:
        return act(values)

    handler.__name__ = endpoint.operation
    handler.__doc__ = endpoint.description
    handler.__signature__ = Signature(  # type: ignore[attr-defined]
        _parameters(descriptor, endpoint), return_annotation=dict[str, Any]
    )
    return handler


def _parameters(
    descriptor: entities.EntityDescriptor, endpoint: entities.Endpoint
) -> list[Parameter]:
    """What the handler takes, in the order the document lists it: the
    path parameters that address the thing, then the body where there is
    one, then the repository."""
    addressed = () if endpoint.verb == entities.READ_ALL else descriptor.addressing
    names = list(addressed)
    if endpoint.verb in (entities.WRITE_SECRET, entities.DELETE_SECRET):
        names.append("slot")
    parameters = [
        Parameter(name, Parameter.POSITIONAL_OR_KEYWORD, annotation=str) for name in names
    ]
    if endpoint.verb in (entities.WRITE, entities.WRITE_SECRET):
        parameters.append(
            Parameter("body", Parameter.POSITIONAL_OR_KEYWORD, annotation=RawBody)
        )
    parameters.append(Parameter("store", Parameter.POSITIONAL_OR_KEYWORD, annotation=StoreDep))
    return parameters


def _act(
    descriptor: entities.EntityDescriptor, endpoint: entities.Endpoint
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """What the route does: one repository call, and one view or one
    acknowledgement.

    Nothing about the configuration is decided here, exactly as nothing
    was decided in the handlers this replaces. Which entry exists is the
    repository's decision, what a read may show is the view's, what a
    write says it did and when it applies is the descriptor's, and what
    is left is the path, the status code and the shape.

    `values` is what the request resolved to, by the names the signature
    declared, so a parameter is read by its name rather than by its
    position.
    """

    def identity(values: dict[str, Any]) -> tuple[str, ...]:
        return tuple(values[parameter] for parameter in descriptor.addressing)

    def slot(values: dict[str, Any]) -> SecretLocation:
        # The identity as a secret location spells it, which is the
        # dotted join of the parameters that address the entity: a
        # provider's stage and name, an MCP server's name.
        return SecretLocation(
            kind=descriptor.secret_slots,  # type: ignore[arg-type]
            identity=".".join(identity(values)),
            slot=values["slot"],
        )

    def read_all(values: dict[str, Any]) -> dict[str, Any]:
        return _collection(descriptor, values["store"])

    def read_one(values: dict[str, Any]) -> dict[str, Any]:
        return views.entity(descriptor.name, descriptor.read(values["store"], *identity(values)))

    def write(values: dict[str, Any]) -> dict[str, Any]:
        descriptor.write(values["store"], *identity(values), values["body"])
        return _acknowledge(descriptor.wrote(*identity(values)), descriptor.notice)

    def delete(values: dict[str, Any]) -> dict[str, Any]:
        descriptor.delete(values["store"], *identity(values))
        return _acknowledge(descriptor.deleted(*identity(values)), descriptor.notice)

    def write_secret(values: dict[str, Any]) -> dict[str, Any]:
        location = slot(values)
        values["store"].set_secret(location, _secret(values["body"]))
        return _acknowledge(wrote_secret(location.describe()), descriptor.notice)

    def delete_secret(values: dict[str, Any]) -> dict[str, Any]:
        location = slot(values)
        values["store"].clear_secret(location)
        return _acknowledge(cleared_secret(location.describe()), descriptor.notice)

    return {
        entities.READ_ALL: read_all,
        entities.READ_ONE: read_one,
        entities.WRITE: write,
        entities.DELETE: delete,
        entities.WRITE_SECRET: write_secret,
        entities.DELETE_SECRET: delete_secret,
    }[endpoint.verb]


def _collection(descriptor: entities.EntityDescriptor, store: ConfigStore) -> dict[str, Any]:
    """Every entry of one kind, keyed the way the kind is addressed. The
    kind addressed by two segments is keyed by both, since two stages
    may hold one name, which is why that listing has a view of its
    own."""
    snapshot = store.load()
    if len(descriptor.addressing) > 1:
        return views.providers(snapshot)
    return views.listing(descriptor.name, snapshot)


def _reads(api: FastAPI) -> None:
    """Every read the API serves.

    Each handler is one repository call and one view, and restates
    nothing: which entity exists is the repository's decision, what a
    read may show is the view's, and what is left here is the path, the
    status code and the shape. They are plain `def`, so FastAPI runs
    them on the threadpool and the synchronous repository never blocks
    the event loop.

    The reads of the commanded kinds are built from their descriptors
    rather than written; the ones written here are the reads the tiers
    do not describe. The whole configuration is every kind at once. A
    device binding and the default agent are a mapping and a scalar,
    written with their own verbs and read without an envelope. The
    pending listing is the running server's own state and reaches no
    database at all.

    An identity rides in the path as one decoded segment, so a name
    carrying a space, a percent sign or a character outside ASCII is
    reached by percent-encoding it and nothing else.
    """

    @api.get("/config", response_model=ConfigDocument, responses=_problems(401, 409, 500))
    def read_config(store: StoreDep) -> dict[str, Any]:
        """The whole domain configuration, masked, with the location of
        every stored secret beside it."""
        return views.config(store.load())

    # Every commanded kind's collection and entry reads, from the
    # registry that says which of them the kind has and what the
    # document says about each.
    _entity_routes(api, READS)

    @api.get("/devices", response_model=dict[str, Envelope], responses=_problems(401, 409, 500))
    def read_devices(store: StoreDep) -> dict[str, Any]:
        """Every device binding, by the canonical form of its MAC. Bound
        devices only: the ones waiting to be claimed are the listing
        below, since they have no binding to show."""
        return views.devices(store.load())

    # Registered before `/devices/{mac}`, and this is the whole reason
    # the two are not written in the order they read: Starlette matches
    # in registration order, so the other way round the literal word
    # `pending` would enter MAC normalization and answer 422.
    @api.get(
        "/devices/pending", response_model=dict[str, PendingDevice], responses=_problems(401)
    )
    def read_pending_devices(pending: PendingDep) -> dict[str, Any]:
        """Every device showing an activation code, by the code it is
        showing.

        Runtime state of the server this application is mounted on
        rather than stored configuration, so it touches no database and
        cannot answer the refusals a read of the configuration can. A
        server that restarts forgets it, and the devices come back
        within a couple of minutes with fresh codes.
        """
        return {device.code: _pending_view(device) for device in pending.listing()}

    @api.get(
        "/devices/{mac}", response_model=Envelope, responses=_problems(401, 404, 409, 422, 500)
    )
    def read_device(mac: str, store: StoreDep) -> dict[str, Any]:
        """One device's binding. The MAC is normalized before it is
        looked up, so `AA-BB-...` and `aa:bb:...` reach the same
        device."""
        return views.device(store.read_device(mac))

    @api.get("/default-agent", response_model=DefaultAgent, responses=_problems(401, 409, 500))
    def read_default_agent(store: StoreDep) -> dict[str, Any]:
        """The agent an unbound device reaches, or null. Unset is a
        configuration rather than a missing entity, so this is never a
        404."""
        return views.default_agent(store.read_default_agent())


def _runtime(api: FastAPI) -> None:
    """What the server around this application is doing, as against what
    is stored.

    A namespace of its own, outside the entity namespaces, because an
    entity name is the operator's to choose: `status` passes the
    `mcp_servers` entry-name rule, an existing database may already hold
    it, and a runtime route under `/mcp-servers/` would shadow it. Kept
    apart, the entity namespaces stay purely CRUD and no runtime route
    added later has to fight a name either.
    """

    @api.get(
        "/runtime/mcp-servers",
        response_model=dict[str, McpServerStatus],
        responses=_problems(401),
    )
    async def read_mcp_server_status(servers: McpServersDep) -> dict[str, McpServerStatus]:
        """What each configured MCP server is doing right now.

        Runtime state of the server this application is mounted on, like
        the pending listing: it touches no database, so it can answer
        none of the refusals a read of the configuration can, and what
        it reports is the slice its managers were built with rather than
        anything read back out of storage.

        `async def`, unlike the repository's plain-`def` handlers, on
        purpose: the read then happens on the event loop that owns the
        managers, so it cannot interleave with a change to them and
        report half of one world.

        An application built without a server around it has no runtime
        to report and answers with an empty object, which is the honesty
        `loaded_agents = ()` already has.
        """
        return {} if servers is None else servers.typed_status()

    @api.get(
        "/runtime/agents/{name}/prompt",
        response_model=AssembledPrompt,
        responses=_problems(
            401,
            404,
            422,
            503,
            instead={
                404: UNLOADED_AGENT_DESCRIPTION,
                422: MALFORMED_REQUEST_DESCRIPTION,
                503: NO_RUNTIME_PROMPT_DESCRIPTION,
            },
        ),
    )
    async def read_agent_prompt(name: str, assemble: AgentPromptDep) -> dict[str, Any]:
        """The system prompt a session opening now as this agent would
        be sent, block by block.

        A runtime read rather than a database one: it is assembled from
        the agents this server loaded, the MCP slice its registry is
        running and the memory store it writes, so it cannot disagree
        with what a session would be given, which is the whole point of
        it.

        It is a preview of a new session and says so, because there is
        no honest per-session answer to offer instead: a conversation
        already in progress holds the know-how half it assembled at its
        own activation, which may predate a reload, and what an operator
        audits is what the configuration produces now.

        `async def`, like the status read beside it: the MCP slice is
        read on the loop that mutates it, so the answer cannot be half
        of one world and half of another. The memory read that follows
        is a file read and happens in a worker thread.

        An agent this server did not load is a 404 naming the restart,
        since an agent is built at boot; an application with no server
        around it answers 503, like the reload.
        """
        if assemble is None:
            raise NoRuntimeError(PROBLEM_DESCRIPTIONS[503])
        assembled = await assemble(name)
        if assembled is None:
            raise UnknownEntityError(UNLOADED_AGENT)
        return {
            "blocks": [
                {
                    "provenance": block.provenance,
                    "name": block.name,
                    "characters": block.characters,
                    "text": block.text,
                }
                for block in assembled.blocks
            ],
            "characters": assembled.characters,
        }

    @api.post(
        "/runtime/mcp-servers/reload",
        response_model=McpReloadResult,
        responses=_problems(
            401, 409, 422, 500, 503, instead={422: RELOAD_REFUSED_DESCRIPTION}
        ),
    )
    async def reload_mcp_servers(
        servers: McpServersDep, reload: McpReloadDep
    ) -> McpReloadResult:
        """Re-read the MCP servers and the agents' grants, and apply
        them to this running server.

        The one action in this namespace, and the one exception to
        "configuration applies at the next start" that a request rather
        than a device asks for. What it re-reads is the `mcp_servers`
        entries, the secrets stored on them and the effective `mcp`
        lists, and nothing else: an agent's prompt, its providers, its
        memory and the whole server section stay as this process booted
        them, so a new agent still waits for the restart that builds it.

        Nothing is stopped or started until every manager the new
        configuration needs has been built, so a refusal (422) has
        changed nothing at all. A server that merely will not connect is
        not a refusal: it applies, and says `down` with its reason
        below, and is reconnected in the background when a session that
        would use it opens.

        Live conversations are not dropped, and when one meets the
        result depends on which half of an entry moved. The tools an
        agent may reach are snapshotted per reply, so a session picks
        those up on its next utterance; a call in flight on a server
        this stopped fails into the same error result a server dropping
        mid-call produces. An entry's `instructions` is prompt text,
        which is assembled at an activation and cached for it, so a
        rewrite reaches a conversation at its next activation, a new
        session or an agent switch. Such an entry is reported
        `unchanged` here, which is a statement about its connection: the
        connection is what did not change.

        One reload runs at a time: a concurrent one is refused with 409
        and has changed nothing, like a write that could not take the
        database's lock.
        """
        # This docstring is the endpoint's description in the committed
        # document, so what belongs to the handler rather than to the
        # contract is said here. What the reload did and what is running
        # afterwards arrive together, composed where the two phases are:
        # taking them apart here would put an invariant of the reload's
        # (no await between the outcomes and the status, so the two
        # halves describe one world) in a request handler, which is the
        # last place able to keep it.
        #
        # The registry is still a dependency, and is read for one thing
        # only: whether there is one. "This application has a running
        # server around it" is the condition this route refuses on, and
        # half a runtime is not it, whatever the composition root
        # happens to pass today. The guard is the endpoint's behavior
        # and not a note about its callers, so it asks about both
        # halves, as it did before the composing left.
        if reload is None or servers is None:
            raise NoRuntimeError(PROBLEM_DESCRIPTIONS[503])
        return await reload()


def _writes(api: FastAPI) -> None:
    """Every write the API serves.

    PUT is create-or-replace, matching the CLI's `set`: the entity's
    model-shaped half is what a write replaces, and its stored secrets
    are not touched, because a fragment cannot carry ciphertext and a
    whole-row replacement would erase them on an ordinary edit. Which is
    the repository's rule, not restated here.

    Nothing about the configuration is decided in this function. A
    handler parses the addressing (which entity, which slot, and for the
    three argument-shaped bodies the one key they carry), calls one
    repository method, and answers with what it did and when it applies.

    The writes of the commanded kinds are built from their descriptors,
    like their reads. What is written here is what those six verbs do
    not describe: binding a device by its MAC or by the code it is
    showing, and setting or clearing the default agent, each with its
    own verb, its own argument-shaped body, and a notice that depends on
    whether the agent it names was loaded.
    """

    # Every commanded kind's writes, deletes and credential slots,
    # from the registry, in the order it lists them.
    _entity_routes(api, WRITES)

    # Before `/devices/{mac}` for the reason the read above is, even
    # though these two paths cannot collide (one segment against two):
    # the pair is easier to keep right as a rule than as a case
    # analysis, and a later route under this prefix inherits it.
    @api.post(
        "/devices/pending/{code}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
        openapi_extra=_request_body(DeviceBinding),
    )
    def add_device(
        code: str,
        body: RawBody,
        store: StoreDep,
        loaded: LoadedAgentsDep,
        pending: PendingDep,
    ) -> dict[str, str]:
        """Bind the device showing this activation code, and retire the
        code.

        The MAC comes from the pending entry, so the operator binds what
        the board in front of them is showing rather than a MAC they
        have to go and find. The write itself is the same repository
        call `PUT /devices/{mac}` makes, so reference checking and
        transactionality are inherited rather than restated, and the
        acknowledgement is the same one, naming the MAC that was bound.

        The claim is atomic: the code is reserved before the write and
        consumed after it, so two operators racing one code produce one
        bind and one retryable refusal. A write that fails releases the
        reservation, leaving the code claimable again, because the
        device is still showing it.
        """
        agents = _agents(body)
        claim = pending.reserve(code)
        if claim.in_flight:
            raise ClaimInFlightError(CODE_IN_FLIGHT)
        if claim.device is None:
            raise UnknownEntityError(UNKNOWN_CODE)
        # A refusal is the repository's decision and its own sentence
        # everywhere else in this file, and here it is the decision but
        # not the sentence. `bind_device` refuses an unresolved
        # reference by naming the agent it could not find, and on this
        # route those names arrived in a request body that a mistake can
        # put a credential into. So the plain refusal is replaced by one
        # naming the field rather than its contents, and the refusals
        # that are not about the request (a busy database, unreadable
        # stored state) travel out as themselves, carrying nothing a
        # caller sent.
        #
        # Recorded and re-raised outside the handler, the rule this
        # codebase settled on: `from None` clears the cause and leaves
        # the context, so the rejected value would still be reachable on
        # the exception that travels out.
        refused = False
        superseded = False
        bound = None
        try:
            bound = store.claim_device(claim.device.mac, agents)
        except DeviceAlreadyBoundError:
            # The code named a device that was unbound when it was
            # issued, and a code outlives the state it was issued in.
            # The entry goes rather than being released: this one is not
            # claimable again by anybody.
            superseded = True
            raise
        except (UnknownEntityError, DatabaseBusyError, StorageError):
            raise
        except ConfigError:
            refused = True
        finally:
            # Every way out but the successful one leaves the device
            # showing its number, so the number has to still work,
            # except the one way that says the number means nothing now.
            if superseded:
                pending.consume(code)
            elif bound is None:
                pending.release(code)
        if refused:
            raise ConfigError(CLAIM_REFUSED)
        pending.consume(code)
        # Both the line and the notice are built from what the row
        # holds, never from what the request sent, exactly as the write
        # by MAC below builds them: a name arriving with spaces around
        # it binds the agent it names, and an acknowledgement derived
        # from the request would have called that agent unloaded and
        # sent the operator to restart a server that is already serving
        # it.
        return _acknowledge(
            bound_device(bound.mac, bound.agents),
            binding_notice(_unloaded(bound.agents, loaded)),
        )

    @api.put(
        "/devices/{mac}",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=_request_body(DeviceBinding),
    )
    def write_device(
        mac: str, body: RawBody, store: StoreDep, loaded: LoadedAgentsDep, pending: PendingDep
    ) -> dict[str, str]:
        """Bind one device to one or more agents. The MAC is normalized
        before it is written, so the two spellings reach one row.

        The running server reads this binding, so the device meets it at
        its next check-in rather than at the next restart; what the
        acknowledgement says depends on whether the agents named are
        ones that server loaded."""
        bound = store.bind_device(mac, _agents(body))
        # This device is configured now, so it is not one an operator
        # may still claim by the code it was showing. Housekeeping
        # rather than a guarantee: a claim itself refuses to bind a
        # device that is already configured, which is what covers a
        # write made where this table cannot be reached, such as the
        # CLI's --local path or a second process.
        pending.retire(bound.mac)
        # Both the line and the notice are built from what the row
        # holds, never from what the request sent: a name arriving with
        # spaces around it binds the agent it names, and an
        # acknowledgement derived from the request would have called
        # that agent unloaded and sent the operator to restart a server
        # that is already serving it.
        return _acknowledge(
            bound_device(bound.mac, bound.agents),
            binding_notice(_unloaded(bound.agents, loaded)),
        )

    @api.delete(
        "/devices/{mac}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_device(mac: str, store: StoreDep) -> dict[str, str]:
        """Remove one device's binding, which with no default agent set
        means the device is refused at the handshake. Live, with no
        agent to be loaded or not: the device stops being served at its
        next check-in, though a conversation already running is left to
        finish."""
        return _acknowledge(deleted_device(store.delete_device(mac)), BINDING_NOTICE)

    @api.put(
        "/default-agent",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=_request_body(DefaultAgentName),
    )
    def write_default_agent(
        body: RawBody, store: StoreDep, loaded: LoadedAgentsDep, pending: PendingDep
    ) -> dict[str, str]:
        """Set the agent an unbound device reaches. Read by the running
        server the way a binding is, so it applies to the next device
        that asks unless the agent it names was written since boot."""
        name = store.set_default_agent(_name(body))
        # A default agent covers every device that has no binding of its
        # own, which is every device in the pending table, so none of
        # them is waiting to be claimed any more. Housekeeping, for the
        # reason the device write above says.
        pending.retire_all()
        return _acknowledge(
            wrote_default_agent(name), binding_notice(_unloaded([name], loaded))
        )

    @api.delete(
        "/default-agent",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 500),
    )
    def remove_default_agent(store: StoreDep) -> dict[str, str]:
        """Unset it, leaving the devices map as the allowlist.
        Idempotent, like the CLI: there is no such thing as a default
        agent that was already not set. Live, like the delete above:
        the next unbound device to ask is turned away."""
        store.clear_default_agent()
        return _acknowledge(CLEARED_DEFAULT_AGENT, BINDING_NOTICE)


def _acknowledge(what: str, notice: str = RESTART_NOTICE) -> dict[str, str]:
    """What a write answers with. The restart sentence is the default
    because it is the contract for everything except the two the running
    server re-reads, and a new write route should have to say that it is
    one of them."""
    return {"wrote": what, "notice": notice}


def _unloaded(agents: Sequence[str], loaded: Collection[str]) -> list[str]:
    """The names a write mentioned that this server has not built an
    agent for, which is what stands between the write and the device."""
    return [name for name in agents if name not in loaded]


def _request_body(model: type[BaseModel]) -> dict[str, Any]:
    """One route's request body, as the document describes it.

    The schema is a reference into `components`, where `_entity_schemas`
    injects these models: nothing in the running application declares
    them, deliberately, so a bare reference would dangle without that
    injection.
    """
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{model.__name__}"}
                }
            },
        }
    }


# The three argument-shaped bodies
#
# Exactly one key, of exactly one shape. Every other body is refused
# with a sentence describing what was expected, and none of them quotes
# what arrived: for the secret write the rejected value is a credential
# often enough that echoing it would make the refusal the leak, and the
# other two are held to the same rule rather than to a weaker one that
# would have to be remembered.
#
# Written with plain checks and no try/except, because a refusal raised
# inside a handler carries the exception being handled as its context,
# and a KeyError or a TypeError raised on a body holds the body.


def _sole_value(body: object, key: str, expectation: str) -> object:
    if not isinstance(body, dict) or set(body) != {key}:
        raise ConfigError(expectation)
    return body[key]


def _agents(body: object) -> list[str]:
    value = _sole_value(body, "agents", DEVICE_BODY)
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise ConfigError(DEVICE_BODY)
    return value


def _name(body: object) -> str:
    value = _sole_value(body, "name", DEFAULT_AGENT_BODY)
    if not isinstance(value, str):
        raise ConfigError(DEFAULT_AGENT_BODY)
    return value


def _secret(body: object) -> str:
    value = _sole_value(body, "secret", SECRET_BODY)
    if not isinstance(value, str) or not value:
        raise ConfigError(SECRET_BODY)
    return value


def mount_api(app: FastAPI, api: FastAPI) -> None:
    """Mount the sub-application so that both /api and /api/ reach it.

    A Mount matches `<prefix>/...` only, so the bare prefix would fall
    through to the router's trailing-slash redirect. A redirect is not
    good enough for a gated namespace: a client that does not resend its
    Authorization header on one would meet a 401 it cannot explain, and
    the redirect itself answers before the gate does.
    """
    app.mount(MOUNT_PATH, api)
    # A Route whose endpoint is not a function is called as ASGI, which
    # is what lets the bare prefix be handed to the same application.
    app.router.routes.append(Route(MOUNT_PATH, _BarePrefix(api)))


class _BarePrefix:
    """The mount prefix without its trailing slash, turned into exactly
    the request `/api/` would have produced."""

    def __init__(self, api: ASGIApp) -> None:
        self._api = api

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        root_path = scope.get("root_path", "")
        rewritten = {
            **scope,
            "root_path": root_path + MOUNT_PATH,
            "path": scope["path"] + "/",
        }
        await self._api(rewritten, receive, send)


class _BearerGate:
    """The token check, in front of routing.

    Compared with `hmac.compare_digest`, which is the point of holding
    the token as one string: an equality test on a secret is a timing
    oracle, and this one is reachable by anyone who can reach the port.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Every HTTP request, matched or not. A scope of any other type
        # is passed through because this application has none of them:
        # it declares no websocket route, so one arriving here reaches a
        # router with nothing to give it.
        if scope["type"] != "http" or self._authorized(scope):
            await self._app(scope, receive, send)
            return
        # WWW-Authenticate is what makes this a 401 rather than a 403: it
        # names the scheme a client should have used.
        response = JSONResponse(
            {"detail": UNAUTHORIZED},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name.lower() != b"authorization":
                continue
            scheme, _, token = value.partition(b" ")
            return scheme.lower() == b"bearer" and hmac.compare_digest(token, self._token)
        return False


class _SanitizedErrors:
    """The last resort: an exception nothing else handled becomes a
    generic 500 with the traceback in the log and nothing of it in the
    body.

    Middleware rather than an `Exception` handler on the application:
    Starlette's error handler re-raises after answering, which in a
    mounted application means the exception continues into the
    device-facing app's own error handling. This ends it here, where the
    response was decided.

    "In the log" means one fixed line naming the exception's class, not
    a traceback and not the exception's own message: this is the point
    where anything a request carried has already been through a handler
    that failed on it, and a log line is as much of a leak as a response
    body when the log is shipped somewhere.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = False

        async def watched(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self._app(scope, receive, watched)
        except Exception as exc:
            # One fixed line, and deliberately not logger.exception. A
            # traceback carries the values that produced it, an
            # exception's own message can be anything a request put in
            # front of it, and the path is request-controlled too. The
            # class name is the most that can be said about a failure
            # here that a request could not have written.
            events.error(
                "the configuration API failed to handle a request (%s)",
                type(exc).__name__,
                event="api_error",
            )
            if started:
                # Half a response is already on the wire, so there is
                # nothing left to say that would not corrupt it. Ended
                # here rather than re-raised: re-raising only reaches an
                # outer logger, which would write the traceback this
                # just took care not to.
                return
            await JSONResponse({"detail": UNEXPECTED}, status_code=500)(scope, receive, send)


def _application() -> FastAPI:
    """The sub-application without its gate: what the server mounts and
    what the document is rendered from, so the two cannot disagree about
    the routes."""
    api = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        # The committed document is the contract. Serving it live, and
        # serving interactive docs, is an additive change the moment a
        # front-end wants it, and until then it is surface with no
        # reader.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        # A mounted application renders its internal paths, so this is
        # how the document says where they actually are.
        servers=[{"url": MOUNT_PATH}],
        # No trailing-slash redirect anywhere in this namespace. The
        # router's default answers `/config/` or `/conversations/` with a
        # 307 whose Location is the request's own path and query string,
        # which puts an entity name, a session id or a rejected cursor in
        # a response header: the one place this API was still quoting
        # back what it was sent, and headers are what proxies and
        # browsers keep. A path with a stray slash is now an unmatched
        # path like any other, which the token holder meets as a 404 and
        # everyone else as the gate's 401.
        #
        # Nothing relied on the redirect. `/api` and `/api/` resolve
        # through the mount instead (`mount_api` below), which is a
        # different mechanism and deliberately not a redirect, and no
        # route this application serves is addressed with a trailing
        # slash by the CLI client or by the committed document.
        redirect_slashes=False,
    )
    api.openapi = _openapi(api)  # type: ignore[method-assign]
    for refusal, status in REFUSAL_STATUS.items():
        api.add_exception_handler(refusal, _refusal(status))
    api.add_exception_handler(RequestValidationError, _malformed_request)
    _reads(api)
    _writes(api)
    _runtime(api)
    # The conversation store's reads, whose route functions live with
    # the store rather than here. Registered on this application and not
    # in `build_api` for the reason this function exists: a route the
    # committed document does not carry is not in the contract, and this
    # is what the document is rendered from. `_problems` travels with
    # them so that module says its refusals in this one's vocabulary
    # without importing it, which would be a cycle.
    conversations.routes(api, _problems)
    return api


def _refusal(status: int) -> Callable[[Request, Exception], Any]:
    """One repository refusal, rendered. The message is the repository's
    own, unchanged from what the CLI prints, so an operator meets one
    vocabulary whichever way they reached it."""

    async def handler(request: Request, exc: Exception) -> JSONResponse:
        if status >= 500:
            # One fixed line naming the exception's class, and never the
            # exception itself: a record's args hold whatever is passed
            # to them, and an exception object carries its message and
            # its chain to anything that walks it. The sentence goes to
            # the caller, which is the channel that was sanitized for
            # it.
            events.error(
                "the configuration API met unreadable stored state (%s)",
                type(exc).__name__,
                event="api_storage_error",
            )
        return JSONResponse({"detail": str(exc)}, status_code=status)

    return handler


async def _malformed_request(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI's own validation, sanitized. Its default 422 echoes the
    rejected input back per error, and a configuration fragment can
    carry a pasted credential, so the whole body is replaced by a
    sentence describing the expectation."""
    return JSONResponse({"detail": MALFORMED_REQUEST}, status_code=422)


def _openapi(api: FastAPI) -> Callable[[], dict[str, Any]]:
    """The document, with the things FastAPI's default generation cannot
    know: the fixed contract version, the mount prefix, the bearer
    scheme (enforcement is middleware, so no dependency carries it), the
    document-level requirement that it applies to every operation, and
    the entity schemas nothing in the running application declares."""

    def openapi() -> dict[str, Any]:
        schema = get_openapi(
            title=api.title,
            version=api.version,
            description=api.description,
            routes=api.routes,
            servers=api.servers,
        )
        components = schema.setdefault("components", {})
        components.setdefault("schemas", {}).update(_entity_schemas())
        _resolve_body_schemas(schema)
        components.setdefault("securitySchemes", {})[BEARER_SCHEME] = {
            "type": "http",
            "scheme": "bearer",
            "description": (
                "The value of the environment variable server.api.secret_env names. "
                "It grants everything this API can do, secret writes included, so it "
                "belongs on a loopback connection or behind TLS."
            ),
        }
        schema["security"] = [{BEARER_SCHEME: []}]
        return schema

    return openapi


def _resolve_body_schemas(schema: dict[str, Any]) -> None:
    """Leave each write's request body as the reference it declared, and
    nothing else.

    FastAPI deep-merges a route's `openapi_extra` into the operation it
    generated, key by key, so the title it gave the opaque body
    parameter survives beside the `$ref` the route declared. A `$ref`
    with a sibling is ignored by some readers and confusing to the rest,
    and the reference is the whole content of these bodies, so the
    leftover goes.
    """
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            content = operation.get("requestBody", {}).get("content", {})
            body = content.get("application/json", {}).get("schema")
            if isinstance(body, dict) and "$ref" in body:
                content["application/json"]["schema"] = {"$ref": body["$ref"]}


def _entity_schemas() -> dict[str, Any]:
    """The entity models as components, each with its nested `$defs`
    hoisted beside it.

    Hoisted because a `$ref` into `components/schemas` has to resolve
    there, and pydantic nests a model's definitions one level down
    inside its own schema. This is the seam the write routes will use:
    their request bodies name these components through `openapi_extra`
    while the running code keeps validating in exactly one place, the
    repository.
    """
    schemas: dict[str, Any] = {}
    for model in ENTITY_MODELS + REQUEST_MODELS:
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    return schemas


__all__ = [
    "API_VERSION",
    "MOUNT_PATH",
    # The shapes, re-exported. They are declared in `responses.py`, one
    # import below FastAPI, so that the CLI can have them without it;
    # they are named here because this is the module that puts them on
    # routes, and it is the name every caller already knows them by.
    "Acknowledgement",
    "AssembledPrompt",
    "ConfigDocument",
    "DefaultAgent",
    "DefaultAgentName",
    "DeviceBinding",
    "Envelope",
    "McpReloadResult",
    "McpServerStatus",
    "PendingDevice",
    "Problem",
    "PromptBlock",
    "SecretSlot",
    "SecretValue",
    "StoredSecretLocation",
    "api_token",
    "build_api",
    "document",
    "mount_api",
    "store_dependency",
]
