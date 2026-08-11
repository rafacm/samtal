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

The gate is ASGI middleware, not a dependency, because a dependency
only runs for a matched route: an unmatched path inside /api would
answer 404 to an unauthenticated caller, which leaks which routes exist
and breaks "the namespace is gated" as a property. Enforcement being
middleware is also why the bearer scheme is stated explicitly in the
document below rather than falling out of a security dependency.

Nothing logs the token, a request body, or an Authorization header.
"""

import hmac
import logging
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from samtal_server.config import views
from samtal_server.config.docgen import API_OPTIONS_NOTE
from samtal_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    StorageError,
    UnknownEntityError,
)
from samtal_server.config.models import (
    API_MOUNT_PATH,
    AgentConfig,
    AgentDefaults,
    Config,
    McpServerConfig,
    ProviderConfig,
)
from samtal_server.config.secrets import load_keys
from samtal_server.config.store import ConfigStore
from samtal_server.db import open_database

logger = logging.getLogger(__name__)

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
    "next server start rather than immediately, and every write says so.\n\n"
    f"{API_OPTIONS_NOTE}"
)

# The name the security scheme is registered under. Referenced by the
# document-level requirement, so both come from one string.
BEARER_SCHEME = "bearerToken"

# What a refusal maps to. Plain ConfigError is the default: a fragment
# whose shape is wrong, a reference that would be left unresolved, a
# slot that is not a credential slot, a stage that is not a stage.
REFUSAL_STATUS: dict[type[ConfigError], int] = {
    UnknownEntityError: 404,
    DatabaseBusyError: 409,
    StorageError: 500,
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

# The entity models the document carries the schemas of. FastAPI
# collects the models its own routes declare, and the write routes will
# declare none: a fragment is received as a raw object and validated in
# the repository, because FastAPI's own validation echoes the input it
# rejected and a fragment can carry a pasted credential. So they are
# injected below instead, which is also what a client that has read an
# envelope needs in order to write one back.
ENTITY_MODELS: tuple[type[BaseModel], ...] = (
    ProviderConfig,
    McpServerConfig,
    AgentConfig,
    AgentDefaults,
)

# How the document describes each refusal a route can answer with. The
# sentence a caller actually receives is the repository's own; these say
# what the status means.
PROBLEM_DESCRIPTIONS: dict[int, str] = {
    401: "The request carried no bearer token, or not the one this server was given.",
    404: "Nothing of that identity exists.",
    409: (
        "Another process holds the configuration database's write lock. Nothing was "
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
}


# The transport shapes
#
# Declared as response models so that the document carries real schemas
# rather than the empty objects an untyped dictionary return would
# produce. They are shapes and not a second validation layer: what
# `views` builds passes through them unchanged, and nothing here decides
# what a read may show.


class SecretSlot(BaseModel):
    """One slot of an entity that holds a secret stored in the database."""

    model_config = ConfigDict(extra="forbid")

    # Nullable and required, not optional: every read answers with the
    # key or with null, and a client that has to tell "no reference" from
    # "the server did not say" has been given a third state it cannot
    # act on.
    shadows: str | None = Field(
        description=(
            "The entity key this stored secret displaces, or null when the entity "
            "writes no reference for the slot. A stored secret takes precedence over "
            "an environment reference written for the same slot, and this names what "
            "it takes the place of."
        ),
    )


class Envelope(BaseModel):
    """One entity as a read returns it: the entity, and its stored-secret
    slots beside it."""

    model_config = ConfigDict(extra="forbid")

    entity: dict[str, Any] = Field(
        description=(
            "The entity's body in the shape a write of it accepts, with every "
            "secret-bearing value masked. Described rather than validated here: a "
            "masked value is not one the entity model would accept back, so the "
            "entity schemas under `components/schemas` are what say which keys a "
            "write may carry."
        )
    )
    secrets: dict[str, SecretSlot] = Field(
        description=(
            "The slots holding a secret stored in the database, by slot name, and "
            "never their values: reads are masked. Empty for the kinds that can hold "
            "no stored secret (agents, agent defaults, devices), so that every read "
            "has one shape."
        )
    )


class StoredSecretLocation(BaseModel):
    """Where one stored secret is, in the whole-configuration read."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description="The kind of entity holding it: provider or mcp_server.")
    identity: str = Field(
        description=(
            "The entity's identity: `<stage>.<name>` for a provider, the name for an "
            "MCP server."
        )
    )
    slot: str = Field(description="The credential slot inside that entity.")
    shadows: str | None = Field(
        description="The entity key this stored secret displaces, or null."
    )


class ConfigDocument(BaseModel):
    """The whole domain configuration of one deployment, masked."""

    model_config = ConfigDict(extra="forbid")

    config: dict[str, Any] = Field(
        description=(
            "The domain half of the configuration (providers, MCP servers, agent "
            "defaults, agents, devices, the default agent) in the shape "
            "`docs/reference/domain-config.md` documents, with every secret-bearing "
            "value masked."
        )
    )
    secrets: list[StoredSecretLocation] = Field(
        description=(
            "Where every secret stored in the database is, which the masked document "
            "above cannot say. A list rather than a mapping, because a location is "
            "three fields and not a key."
        )
    )


class DefaultAgent(BaseModel):
    """The agent an unbound device reaches."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        description=(
            "The default agent's name, or null when none is set, which leaves the "
            "devices map as the allowlist."
        ),
    )


class Problem(BaseModel):
    """A refusal, in the repository's own words."""

    model_config = ConfigDict(extra="forbid")

    detail: str = Field(
        description=(
            "What was refused and why, the same sentence the `samtal-server config` "
            "command prints for it. It names the entity the request addressed and "
            "the rule that was broken; it never quotes a secret or a configuration "
            "value that was rejected."
        )
    )


def build_api(token: str, database_dir: Path) -> FastAPI:
    """The sub-application the server mounts: the routes, gated.

    `token` is compared against every request's bearer token, and is
    resolved once at app build by `api_token` below rather than read per
    request, so a deployment that forgot the variable is refused at boot
    instead of at the first call.
    """
    api = _application()
    # Attached rather than closed over: the read and write routes take
    # it with Depends(...), and milestone 1 has none of them yet.
    api.state.store = store_dependency(database_dir)
    # Added last is outermost, so a failure inside the gate itself
    # answers as sanitized as one inside a handler.
    api.add_middleware(_BearerGate, token=token)
    api.add_middleware(_SanitizedErrors)
    return api


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
    yield from request.app.state.store()


StoreDep = Annotated[ConfigStore, Depends(_store)]


def _problems(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """The refusals a route can answer with, as the document describes
    them. Declaring 422 here also replaces FastAPI's own
    validation-error response, which describes a body shape the
    sanitized handler never sends."""
    return {
        status: {"model": Problem, "description": PROBLEM_DESCRIPTIONS[status]}
        for status in statuses
    }


def _reads(api: FastAPI) -> None:
    """Every read the API serves.

    Each handler is one repository call and one view, and restates
    nothing: which entity exists is the repository's decision, what a
    read may show is the view's, and what is left here is the path, the
    status code and the shape. They are plain `def`, so FastAPI runs
    them on the threadpool and the synchronous repository never blocks
    the event loop.

    An identity rides in the path as one decoded segment, so a name
    carrying a space, a percent sign or a character outside ASCII is
    reached by percent-encoding it and nothing else.
    """

    @api.get("/config", response_model=ConfigDocument, responses=_problems(401, 409, 500))
    def read_config(store: StoreDep) -> dict[str, Any]:
        """The whole domain configuration, masked, with the location of
        every stored secret beside it."""
        return views.config(store.load())

    @api.get(
        "/providers",
        response_model=dict[str, dict[str, Envelope]],
        responses=_problems(401, 409, 500),
    )
    def read_providers(store: StoreDep) -> dict[str, Any]:
        """Every provider, by stage and then by name: a provider is
        addressed by the two together, since two stages may hold one
        name."""
        return views.providers(store.load())

    @api.get(
        "/providers/{stage}/{name}",
        response_model=Envelope,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def read_provider(stage: str, name: str, store: StoreDep) -> dict[str, Any]:
        """One provider."""
        return views.provider(store.read_provider(stage, name))

    @api.get(
        "/mcp-servers", response_model=dict[str, Envelope], responses=_problems(401, 409, 500)
    )
    def read_mcp_servers(store: StoreDep) -> dict[str, Any]:
        """Every MCP server, by name."""
        return views.mcp_servers(store.load())

    @api.get(
        "/mcp-servers/{name}",
        response_model=Envelope,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def read_mcp_server(name: str, store: StoreDep) -> dict[str, Any]:
        """One MCP server."""
        return views.mcp_server(store.read_mcp_server(name))

    @api.get("/agents", response_model=dict[str, Envelope], responses=_problems(401, 409, 500))
    def read_agents(store: StoreDep) -> dict[str, Any]:
        """Every agent, by name."""
        return views.agents(store.load())

    @api.get(
        "/agents/{name}", response_model=Envelope, responses=_problems(401, 404, 409, 422, 500)
    )
    def read_agent(name: str, store: StoreDep) -> dict[str, Any]:
        """One agent."""
        return views.agent(store.read_agent(name))

    @api.get("/agent-defaults", response_model=Envelope, responses=_problems(401, 409, 500))
    def read_agent_defaults(store: StoreDep) -> dict[str, Any]:
        """What every agent uses unless it names something else. One
        entry for the whole deployment, and never missing: an unwritten
        one reads as the empty entry."""
        return views.agent_defaults(store.read_agent_defaults())

    @api.get("/devices", response_model=dict[str, Envelope], responses=_problems(401, 409, 500))
    def read_devices(store: StoreDep) -> dict[str, Any]:
        """Every device binding, by the canonical form of its MAC."""
        return views.devices(store.load())

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
            logger.error(
                "the configuration API failed to handle a request (%s)",
                type(exc).__name__,
                extra={"event": "api_error"},
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
    )
    api.openapi = _openapi(api)  # type: ignore[method-assign]
    for refusal, status in REFUSAL_STATUS.items():
        api.add_exception_handler(refusal, _refusal(status))
    api.add_exception_handler(RequestValidationError, _malformed_request)
    _reads(api)
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
            logger.error(
                "the configuration API met unreadable stored state (%s)",
                type(exc).__name__,
                extra={"event": "api_storage_error"},
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
    for model in ENTITY_MODELS:
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    return schemas


__all__ = [
    "API_VERSION",
    "MOUNT_PATH",
    "api_token",
    "build_api",
    "document",
    "mount_api",
    "store_dependency",
]
