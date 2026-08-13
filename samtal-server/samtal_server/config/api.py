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
from collections.abc import Awaitable, Callable, Collection, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import Body, Depends, FastAPI, Request
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
    DeviceAlreadyBoundError,
    ReloadInProgressError,
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
from samtal_server.config.secrets import SecretLocation, load_keys
from samtal_server.config.store import ConfigStore
from samtal_server.config.writes import (
    BINDING_NOTICE,
    CLEARED_DEFAULT_AGENT,
    RESTART_NOTICE,
    WROTE_AGENT_DEFAULTS,
    binding_notice,
    bound_device,
    cleared_secret,
    deleted_agent,
    deleted_device,
    deleted_mcp_server,
    deleted_provider,
    wrote_agent,
    wrote_default_agent,
    wrote_mcp_server,
    wrote_provider,
    wrote_secret,
)
from samtal_server.db import open_database

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

    # Named here for the same reason and with more force: the MCP
    # registry imports the SDK's clients and this project's provider
    # layer, none of which rendering a document has any business
    # loading. It arrives as an argument and is read through one method.
    from samtal_server.tools.mcp import McpServers

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
    "`POST /runtime/mcp-servers/reload` is the second exception to the boot-time "
    "snapshot, and unlike device bindings it is asked for rather than noticed. It "
    "re-reads the `mcp_servers` entries, the secrets stored on them and the agents' "
    "`mcp` grant lists, and applies them to the running server: entries are started, "
    "restarted, stopped or left alone, and live conversations pick the result up on "
    "their next utterance without being dropped. Everything else about an agent "
    "still waits for a restart, which is why writes to those keep saying so.\n\n"
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

# The three bodies that are arguments rather than fragments, as the
# document describes them. The models below are documentation and
# nothing else: they are injected into `components` and named by the
# routes' `openapi_extra`, and they are deliberately not declared as
# body types, for the reason the entity models are not either. What
# enforces them at runtime is the exact-shape parser further down, which
# describes the expectation and never echoes what it refused.


class DeviceBinding(BaseModel):
    """What a device write carries: the agents the device may reach."""

    model_config = ConfigDict(extra="forbid")

    agents: list[str] = Field(
        description=(
            "The agents this device is bound to, by name. The first is the agent a "
            "conversation starts on and the rest are the ones switch_agent can reach. "
            "Every name has to be an agent that exists, or the write is refused."
        )
    )


class DefaultAgentName(BaseModel):
    """What a default-agent write carries. Clearing it is the DELETE,
    not a null here: one way to say a thing."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description=(
            "The agent an unbound device reaches. It has to be an agent that exists. "
            "To unset it, DELETE this resource, which leaves the devices map as the "
            "allowlist."
        )
    )


class SecretValue(BaseModel):
    """What a secret write carries: the credential itself, the only
    plaintext this API ever accepts."""

    model_config = ConfigDict(extra="forbid")

    secret: str = Field(
        # The runtime parser refuses an empty string, and so does the
        # repository underneath it, so the document says the same: a
        # contract that permits what the API refuses is one a client
        # generator would build the wrong request from.
        min_length=1,
        description=(
            "The credential, in plaintext, stored encrypted under the newest key in "
            "SAMTAL_MASTER_KEY. It crosses the connection as itself, which is why the "
            "whole API belongs on a loopback connection or behind TLS. It is never "
            "read back: a read names the slot and masks the value. It may not be "
            "empty."
        ),
    )


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


class PendingDevice(BaseModel):
    """One device waiting to be claimed, as the listing shows it.

    The listing is keyed by the code, because the code is what the
    operator has: they are holding a board with six digits on it, and
    the question the board model and the firmware version answer is
    which of these entries is that board.
    """

    model_config = ConfigDict(extra="forbid")

    mac: str = Field(
        description=(
            "The device's MAC in canonical form, which is the row a successful claim "
            "writes."
        )
    )
    client_id: str = Field(
        description="The UUID the device sent as its Client-Id at its last check-in."
    )
    board: str = Field(
        description=(
            "The board type the device reported, such as "
            "waveshare-esp32-s3-touch-lcd-1.54, or `unknown` when it reported none. "
            "Whatever the device said, bounded in length and stripped of anything "
            "unprintable."
        )
    )
    firmware: str = Field(
        description=(
            "The firmware version the device reported, or 0.0.0 when it reported none."
        )
    )
    first_seen: str = Field(
        description="When this device first checked in, as an ISO-8601 instant in UTC."
    )
    last_seen: str = Field(description="Its most recent check-in, in the same form.")
    expires_at: str = Field(
        description=(
            "When this code stops being claimable. The device re-checks every couple of "
            "minutes and displays whatever the fresh reply carries, so an expired code "
            "is replaced on the screen rather than leaving the device stranded."
        )
    )


class McpServerStatus(BaseModel):
    """One configured MCP server, as the running server sees it.

    The listing is keyed by the entry's name, because that is what the
    operator wrote and what every tool the server publishes is prefixed
    with.
    """

    model_config = ConfigDict(extra="forbid")

    state: Literal["connected", "down", "unused"] = Field(
        description=(
            "What this entry is doing: `connected` and offering the tools below, "
            "`down` and offering none, or `unused` because no agent references it, so "
            "this server never built a connection for it at all."
        )
    )
    reason: str | None = Field(
        description=(
            "Why a `down` server is down, as a fixed token this server owns: the class "
            "of the failure, or `DroppedAfterFailedCall` for a connection dropped after "
            "a tool call failed on it. Null when it is not down. Never a message the "
            "far side wrote, since an MCP server is a third party and its bytes are not "
            "this API's to publish."
        )
    )
    since: str = Field(
        description=(
            "When this state was last entered, as an ISO-8601 instant in UTC. A new "
            "reason for staying down counts as entering it again, since it is a fresh "
            "failure. For an entry no agent references it is when the running "
            "configuration took effect."
        )
    )
    tools: list[str] = Field(
        description=(
            "What this server published, under the names the model is given "
            "(`<entry>__<tool>`, sanitized). Empty while it is down. Only names cross "
            "this surface: a description, or the name a server listed before the "
            "publishing rule got to it, is bytes that server chose, and a server "
            "holding a credential of this deployment's could reflect it in either."
        )
    )
    grants: dict[str, list[str] | None] = Field(
        description=(
            "Which agents may reach this server, by agent name. The value is how much "
            "of the server the agent gets: null is all of it, which is every grant "
            "today."
        )
    )


class McpReloadResult(BaseModel):
    """What one reload did, and what is running once it had done it.

    Both halves in one answer on purpose: the request that applies a
    change is the request that says what the change was and how it came
    out, so believing a write took effect when it did not takes a
    deliberate act of not reading the reply.
    """

    model_config = ConfigDict(extra="forbid")

    started: list[str] = Field(
        description=(
            "The entries that had no connection before this reload and have one now: "
            "newly written, or newly named by some agent's `mcp` list. Started is not "
            "connected: an entry here whose server was unreachable is `down` below, "
            "with its reason."
        )
    )
    restarted: list[str] = Field(
        description=(
            "The entries whose fragment or whose stored secrets changed. Their "
            "connections were closed and made again, so a rotated credential applies "
            "here rather than at the next server start."
        )
    )
    stopped: list[str] = Field(
        description=(
            "The entries this server no longer connects: deleted, or no longer named "
            "by any agent. A deleted one is gone from the status below; a "
            "de-referenced one is still there, as `unused`."
        )
    )
    unchanged: list[str] = Field(
        description=(
            "The entries nothing changed about, which kept the connections they had. A "
            "reload does not disturb them, so the conversations using them do not "
            "notice one."
        )
    )
    servers: dict[str, McpServerStatus] = Field(
        description=(
            "What every configured entry is doing now that the reload has been applied, "
            "keyed by entry name: exactly what `GET /runtime/mcp-servers` answers, "
            "taken in the same breath so that applying and verifying are one round "
            "trip."
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


class Acknowledgement(BaseModel):
    """What a write answers with: what it did, and when it takes
    effect."""

    model_config = ConfigDict(extra="forbid")

    wrote: str = Field(
        description=(
            "What was written or deleted, naming the entity the way the "
            "`samtal-server config` command names it in the line it prints."
        )
    )
    notice: str = Field(
        description=(
            "When the change takes effect, as one of two sentences. Configuration is a "
            "boot-time snapshot, so most writes apply at the next server start. A "
            "device binding and the default agent are read by the running server, so "
            "they apply at the device's next OTA check or connection with no restart, "
            "unless they name an agent this server has not loaded, which is the case "
            "that carries the restart sentence again."
        )
    )


def build_api(
    token: str,
    database_dir: Path,
    loaded_agents: Collection[str] = (),
    pending: "PendingDevices | None" = None,
    mcp_servers: "McpServers | None" = None,
    mcp_reload: Callable[[], Awaitable[Any]] | None = None,
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
    """
    api = _application()
    # Attached rather than closed over: the read and write routes take
    # it with Depends(...), and milestone 1 has none of them yet.
    api.state.store = store_dependency(database_dir)
    api.state.loaded_agents = frozenset(loaded_agents)
    api.state.pending = pending if pending is not None else _empty_pending()
    api.state.mcp_servers = mcp_servers
    api.state.mcp_reload = mcp_reload
    # Added last is outermost, so a failure inside the gate itself
    # answers as sanitized as one inside a handler.
    api.add_middleware(_BearerGate, token=token)
    api.add_middleware(_SanitizedErrors)
    return api


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
    yield from request.app.state.store()


StoreDep = Annotated[ConfigStore, Depends(_store)]


def _loaded_agents(request: Request) -> frozenset[str]:
    """Which agents the server around this application loaded at boot.

    Taken from the application for the reason the store is: the document
    is rendered from an application built without a server, and nothing
    a route declares may depend on there being one.
    """
    return request.app.state.loaded_agents


LoadedAgentsDep = Annotated[frozenset[str], Depends(_loaded_agents)]


def _pending(request: Request) -> "PendingDevices":
    """The devices waiting to be claimed, from the server this
    application is mounted on. Taken from the application for the reason
    the store is."""
    return request.app.state.pending


# Annotated `Any` rather than the real type on purpose: FastAPI resolves
# a route's annotations at import, so a forward reference to a class
# this module deliberately does not import at runtime would fail to
# resolve. The dependency function above carries the honest type.
PendingDep = Annotated[Any, Depends(_pending)]


def _mcp_servers(request: Request) -> "McpServers | None":
    """The running server's MCP managers, or None for an application
    built without a server around it. Taken from the application for the
    reason the store is."""
    return request.app.state.mcp_servers


# Annotated `Any` for the reason PendingDep is.
McpServersDep = Annotated[Any, Depends(_mcp_servers)]


def _mcp_reload(request: Request) -> Callable[[], Awaitable[Any]] | None:
    """What applies a re-read of the stored configuration to the running
    MCP managers, or None for an application built without a server.
    Taken from the application for the reason the store is."""
    return request.app.state.mcp_reload


McpReloadDep = Annotated[Any, Depends(_mcp_reload)]


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
    async def read_mcp_server_status(servers: McpServersDep) -> dict[str, Any]:
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
        return {} if servers is None else servers.status()

    @api.post(
        "/runtime/mcp-servers/reload",
        response_model=McpReloadResult,
        responses=_problems(401, 409, 422, 500, 503),
    )
    async def reload_mcp_servers(
        servers: McpServersDep, reload: McpReloadDep
    ) -> dict[str, Any]:
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

        Live conversations are not dropped. The tools an agent may reach
        are snapshotted per reply, so a session picks the new world up on
        its next utterance; a call in flight on a server this stopped
        fails into the same error result a server dropping mid-call
        produces.

        One reload runs at a time: a concurrent one is refused with 409
        and has changed nothing, like a write that could not take the
        database's lock.
        """
        if reload is None or servers is None:
            raise NoRuntimeError(PROBLEM_DESCRIPTIONS[503])
        applied = await reload()
        # Taken here rather than answered by the registry, and with no
        # await between the two, so the outcomes and the status describe
        # one world.
        return {
            "started": list(applied.started),
            "restarted": list(applied.restarted),
            "stopped": list(applied.stopped),
            "unchanged": list(applied.unchanged),
            "servers": servers.status(),
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
    """

    @api.put(
        "/providers/{stage}/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=_request_body(ProviderConfig),
    )
    def write_provider(
        stage: str, name: str, body: RawBody, store: StoreDep
    ) -> dict[str, str]:
        """Create or replace one provider from a fragment in the shape
        the YAML section had."""
        store.set_provider(stage, name, body)
        return _acknowledge(wrote_provider(stage, name))

    @api.delete(
        "/providers/{stage}/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_provider(stage: str, name: str, store: StoreDep) -> dict[str, str]:
        """Delete one provider, and the secrets stored on it. Refused
        while an agent or the agent defaults still name it."""
        store.delete_provider(stage, name)
        return _acknowledge(deleted_provider(stage, name))

    @api.put(
        "/providers/{stage}/{name}/secrets/{slot}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
        openapi_extra=_request_body(SecretValue),
    )
    def write_provider_secret(
        stage: str, name: str, slot: str, body: RawBody, store: StoreDep
    ) -> dict[str, str]:
        """Store one of this provider's credentials, encrypted. The slot
        is the option name the credential fills, such as api_key; a
        stored secret takes precedence over an environment reference
        written for the same slot."""
        location = SecretLocation.provider(stage, name, slot)
        store.set_secret(location, _secret(body))
        return _acknowledge(wrote_secret(location.describe()))

    @api.delete(
        "/providers/{stage}/{name}/secrets/{slot}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_provider_secret(
        stage: str, name: str, slot: str, store: StoreDep
    ) -> dict[str, str]:
        """Remove one stored credential. A slot holding none is a 404."""
        location = SecretLocation.provider(stage, name, slot)
        store.clear_secret(location)
        return _acknowledge(cleared_secret(location.describe()))

    @api.put(
        "/mcp-servers/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=_request_body(McpServerConfig),
    )
    def write_mcp_server(name: str, body: RawBody, store: StoreDep) -> dict[str, str]:
        """Create or replace one MCP server."""
        store.set_mcp_server(name, body)
        return _acknowledge(wrote_mcp_server(name))

    @api.delete(
        "/mcp-servers/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_mcp_server(name: str, store: StoreDep) -> dict[str, str]:
        """Delete one MCP server, and the secrets stored on it."""
        store.delete_mcp_server(name)
        return _acknowledge(deleted_mcp_server(name))

    @api.put(
        "/mcp-servers/{name}/secrets/{slot}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
        openapi_extra=_request_body(SecretValue),
    )
    def write_mcp_secret(
        name: str, slot: str, body: RawBody, store: StoreDep
    ) -> dict[str, str]:
        """Store one of this MCP server's credentials, encrypted. The
        slot is `env.<KEY>` or `headers.<Key>`, which is where the value
        would otherwise have been written as a $VAR reference."""
        location = SecretLocation.mcp_server(name, slot)
        store.set_secret(location, _secret(body))
        return _acknowledge(wrote_secret(location.describe()))

    @api.delete(
        "/mcp-servers/{name}/secrets/{slot}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_mcp_secret(name: str, slot: str, store: StoreDep) -> dict[str, str]:
        """Remove one stored credential."""
        location = SecretLocation.mcp_server(name, slot)
        store.clear_secret(location)
        return _acknowledge(cleared_secret(location.describe()))

    @api.put(
        "/agents/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=_request_body(AgentConfig),
    )
    def write_agent(name: str, body: RawBody, store: StoreDep) -> dict[str, str]:
        """Create or replace one agent. Every provider and MCP server it
        names has to exist already, which is what the natural creation
        order is about."""
        store.set_agent(name, body)
        return _acknowledge(wrote_agent(name))

    @api.delete(
        "/agents/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_agent(name: str, store: StoreDep) -> dict[str, str]:
        """Delete one agent. Refused while a device binding or the
        default agent still names it."""
        store.delete_agent(name)
        return _acknowledge(deleted_agent(name))

    @api.put(
        "/agent-defaults",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=_request_body(AgentDefaults),
    )
    def write_agent_defaults(body: RawBody, store: StoreDep) -> dict[str, str]:
        """Replace what every agent uses unless it names something else.
        One entry for the whole deployment, so this is a replace and
        there is nothing to delete."""
        store.set_agent_defaults(body)
        return _acknowledge(WROTE_AGENT_DEFAULTS)

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
    _writes(api)
    _runtime(api)
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
    "api_token",
    "build_api",
    "document",
    "mount_api",
    "store_dependency",
]
