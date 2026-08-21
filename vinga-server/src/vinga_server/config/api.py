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

import contextlib
import hmac
import os
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Iterator,
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from cryptography.fernet import MultiFernet
from fastapi import Body, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Connection, Engine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Route
from starlette.types import ASGIApp, Lifespan, Message, Receive, Scope, Send

from vinga_server.config import entities, views
from vinga_server.config.docgen import API_OPTIONS_NOTE
from vinga_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    DeviceAlreadyBoundError,
    ProviderRefusedError,
    ReloadInProgressError,
    RunningConfigMovedError,
    SnapshotOnlyError,
    StorageError,
    UnknownEntityError,
)
from vinga_server.config.models import (
    API_MOUNT_PATH,
    Config,
    FieldProblem,
)
from vinga_server.config.responses import (
    Acknowledgement,
    AssembledPrompt,
    ConfigDiff,
    ConfigDiffReader,
    ConfigDocument,
    ConfigReloader,
    ConfigReloadResult,
    DefaultAgent,
    DefaultAgentName,
    DeviceBinding,
    Envelope,
    FieldError,
    McpServerStatus,
    McpStatusSource,
    PendingDevice,
    Problem,
    PromptBlock,
    SecretSlot,
    SecretValue,
    StoredSecretLocation,
)
from vinga_server.config.secrets import MASK, SecretLocation, load_keys, provider_identity
from vinga_server.config.store import ConfigStore
from vinga_server.config.writes import (
    CLEARED_DEFAULT_AGENT,
    RESTART_NOTICE,
    binding_notice,
    bound_device,
    cleared_secret,
    deleted_agent,
    deleted_device,
    deleted_mcp_server,
    deleted_prompt_fragment,
    deleted_provider,
    wrote_agent,
    wrote_agent_defaults,
    wrote_default_agent,
    wrote_mcp_server,
    wrote_prompt_fragment,
    wrote_provider,
    wrote_secret,
)
from vinga_server.conversations import api as conversations
from vinga_server.db import open_database
from vinga_server.events import ServerEvents
from vinga_server.events.catalog import ApiError, ApiStorageError
from vinga_server.events.values import ClassName

# The pending table, imported like anything else since issue #143 split
# the onboarding package. It used to be a forward reference, because the
# module that held the table also held a router over the OTA handlers,
# and so pulled the websocket session and everything a conversation
# needs.
#
# What naming it costs now, stated exactly rather than as "nothing":
# `pending.py` itself reads only the standard library, but reaching it
# runs the package's `__init__`, which also loads `keys`, `origin` and
# `unbound`, and `unbound` names the device bindings view, which brings
# SQLAlchemy and the configuration store. Accepted, and cheaply: this
# module imports `open_database` just above, so SQLAlchemy is already
# here whatever the pending table does. The line that matters
# is the conversation stack, and that is the line
# `tests/unit/test_onboarding_import_weight.py` holds, against
# `document()` itself rather than against the import alone.
#
# The MCP registry was deferred here too, for the same reason and with
# more force: it imports the SDK's clients and this project's provider
# layer, none of which rendering a document has any business loading. It
# is not named anywhere any more. What the routes are handed is
# `McpStatusSource` and `ConfigReloader` from `responses.py`, which say what
# this application asks of a running server out of typing and the
# response models, so the annotations resolve at import and the
# constraint holds by construction.
from vinga_server.onboarding.pending import PendingDevice as PendingRecord
from vinga_server.onboarding.pending import PendingDevices

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

API_TITLE = "vinga-server configuration API"

API_DESCRIPTION = (
    "The domain half of one vinga-server deployment's configuration: providers, "
    "MCP servers, agents, devices, and the credentials they use. Every request "
    "carries a bearer token, whose value is the environment variable "
    "`server.api.secret_env` names.\n\n"
    "A read of one entity is a write of it. The `entity` half of the envelope an "
    "addressed read answers is resubmittable as it stands: a PUT replaces the "
    "model-shaped half and never the credentials stored beside it, so an edit is "
    "read it, change it, send the whole of it back, and the fields a read leaves "
    "out are the ones that mean absence and mean the same absence on the way back. "
    "An environment reference reads back as itself and resubmits as itself. A value "
    f"shown as the mask, `{MASK}`, resubmits as keep the stored value, which is "
    "substituted before the fragment is validated; that mask written where nothing "
    "is stored is refused, naming the field where the field is one this API "
    "declares. An "
    "unchanged stored secret needs no action at all: the envelope's `secrets` "
    "mapping is informational, and rotating a credential is the secret PUT, which "
    "is the one door a plaintext value enters by. Everything else a read answers is "
    "display-only, and named here so the writable category has a stated boundary: "
    "that `secrets` mapping; the identity keys of every listing, which address the "
    "target URL rather than belonging in a body; both halves of the "
    "whole-configuration read, which has no PUT of its own; the pending-device "
    "listing, which is the running server's state; and everything under `/runtime` "
    "and `/conversations`, which is not stored configuration at all. The writable "
    "shapes are that one `entity` and the three bodies this document describes as "
    "arguments rather than as entities: a device's `agents`, the default agent's "
    "`name`, and a credential's `secret`.\n\n"
    "A write is stored, and when it reaches a running server depends on the kind. "
    "Part of the configuration is read once at start and served until the next one: "
    "the agent set, `agent_defaults`, which provider entry serves each of an agent's "
    "stages, and the whole server section, which is this process's own file and is "
    "never re-read. The rest converges at one "
    "of two other boundaries. Device bindings and the default agent are read as a "
    "device asks for them, so binding or unbinding a device applies at that device's "
    "next OTA check or connection; that exception ends where the agent does, since a "
    "server composes an agent's pipeline at start, so a binding naming an agent this "
    "server has not loaded waits for the restart that loads it. The provider entries "
    "and the MCP entries with the secrets stored on them, the agents' `mcp` grant "
    "lists, the shared prompt "
    "fragments, each agent's own prompt text and each agent's own filled pauses are "
    "applied by "
    "`POST /runtime/config/reload`, without a restart and without dropping a "
    "conversation. Every write says which of these happened, in its `notice`, and an "
    "agent write says two things because an agent entry's fields fall on both sides "
    "of that line, each applied field with the moment it converges at.\n\n"
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
    "`GET /runtime/config/diff` is the third read there, and the one that spans both "
    "sides: what the database holds that this server is not serving, kind by kind, as "
    "the entity names added, removed and changed, each kind carrying the boundary its "
    "changes converge at. `restart` is what this process read once and serves until it "
    "is started again; `reload` is what the apply below puts in place, which is the "
    "provider entries, the MCP entries, the agents' grants, the shared prompt "
    "fragments, each agent's own "
    "prompt text and each agent's own filler section; `check-in` is the device "
    "bindings and the default agent, which a "
    "device is answered as it asks and which are therefore never pending, so they "
    "carry the label alone and no lists. The agents' entry spans all three, so their "
    "kind carries three entries of its own beside the restart-bound lists: `grants`, "
    "`prompt` and `filler`, each labelled `reload`. Changed means the stored state "
    "differs from the "
    "comparison baseline rather than that something was written: an edit changed back "
    "before anyone looked produces no diff, and a rewritten stored secret counts as "
    "different, because what is compared is an opaque mark over the ciphertext and it "
    "moves even when the plaintext may not have. Entity names and those labels are the "
    "whole of the answer: no bodies, no values and no marks cross it. The MCP half is "
    "compared against the entries running now rather than the ones this process booted "
    "with, so a change a reload has already applied is not reported as pending. What "
    "an answer does not say is that applying what it lists would succeed: it compares "
    "configuration and connects nothing, while the reload below goes on to build a "
    "server for every entry an agent references and can still refuse on one of "
    "those.\n\n"
    "`POST /runtime/config/reload` is the one action in that namespace, and unlike "
    "device bindings it is asked for rather than noticed. It re-reads the stored "
    "configuration and applies every kind it can apply while the process runs: the "
    "`providers` entries and the `mcp_servers` entries with the secrets stored on "
    "them, the agents' `mcp` grant "
    "lists, the shared prompt fragments, each agent's own prompt text and includes, "
    "and each agent's own filler section. "
    "Entries are started, restarted, stopped or left alone, and no "
    "conversation is dropped. Nothing is swapped, stopped or started until the whole "
    "new world has been composed, validated and built, so a refusal has changed "
    "nothing at all. When a live conversation meets the result depends on which half "
    "moved. The tools it may reach are snapshotted per reply, so a started, restarted "
    "or stopped entry is picked up on its next utterance. Prompt text is assembled at "
    "an activation and cached for it, so an agent's prompt, a fragment it includes and "
    "an entry's `instructions` alike reach a conversation at its next activation, "
    "which is a new session or an agent switch, and never a reply of one already "
    "running; `GET /runtime/agents/{name}/prompt` previews what a session opening now "
    "would be sent. Filled pauses are synthesized during the apply and bound by a "
    "conversation when it opens, so an edited filler section reaches the next "
    "conversation and never changes what one already open is masking with. An agent is "
    "synthesized again when any field of its effective `filler` section moved or when "
    "the voice that speaks it did, and its clips are carried over as they are "
    "otherwise: the whole section is the unit of comparison, so an edit to `delay_ms` "
    "alone is a round of text-to-speech work at the configured provider even though "
    "the audio it produces is identical, and rewriting the provider entry an agent "
    "speaks through is another, since that is the voice moving; an edit that reaches "
    "neither, a prompt or a fragment, is none. An agent whose synthesis failed applies "
    "with no clip and runs unmasked rather than refusing the reload, which the "
    "answer's `fillers` section reports. The engines are the same clock and a "
    "different cost: an entry whose definition and stored credential are unchanged is "
    "carried into the new world as the object it already was, so an edit to a prompt "
    "reloads no model at all, while a rewritten entry is built before anything is "
    "swapped and the conversations that open after that speak through it. One that a "
    "conversation is still speaking through is released when that conversation ends, "
    "so an apply that changes a local model briefly holds two, and one whose engines "
    "would not build refuses with nothing changed. Which entry serves each of an "
    "agent's stages, the agent set and `agent_defaults` still wait for a restart, "
    "which is why writes to those keep saying so.\n\n"
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
    # And the fourth, which is neither held nor wrong but too late: a
    # read that has to describe one running world found the world had
    # moved under it, and refused rather than answer with two.
    RunningConfigMovedError: 409,
    # The fifth is the odd one out and shares the status anyway, because
    # what it has in common with the four is the whole of what a status
    # says: nothing was changed. A server composed from a snapshot has
    # no store describing its world, so the two surfaces that span both
    # sides have nothing to span. Retrying will not help, and unlike the
    # others its sentence says so.
    SnapshotOnlyError: 409,
    StorageError: 500,
    NoRuntimeError: 503,
    # The sixth is an ordinary 422 with a type of its own, which is what
    # it is for: an apply that could not build the engines the stored
    # world names is a stored half this server cannot serve, exactly
    # like one that will not compose, and the separate type is what lets
    # its own fixed sentence through the composition root's rewrite
    # rather than being replaced by the general one.
    ProviderRefusedError: 422,
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

# And the refusal shape, injected for the same reason since the refusal
# declarations stopped naming it as a response model: they name its
# schema in `components` instead, so that each of them can carry exactly
# one content type, the problem media type.
PROBLEM_MODELS: tuple[type[BaseModel], ...] = (Problem, FieldError)

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
    "use that. `vinga-server config pending` lists the codes this server is showing "
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
    "`vinga-server config list` to see the agents that exist."
)

# How the document describes each refusal a route can answer with. The
# sentence a caller actually receives is the repository's own; these say
# what the status means.
PROBLEM_DESCRIPTIONS: dict[int, str] = {
    401: "The request carried no bearer token, or not the one this server was given.",
    404: "Nothing of that identity exists.",
    405: (
        "That path is a route, but not for this method. The `Allow` header names the "
        "methods it does serve."
    ),
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

# And what each of them is called: the status's standard HTTP reason
# phrase, which is what RFC 9457 asks a problem with no `type` to carry.
# Beside the descriptions and keyed by the same statuses, so the two
# things this API says about a status live in one place; a test holds
# the key sets equal.
PROBLEM_TITLES: dict[int, str] = {
    401: "Unauthorized",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    422: "Unprocessable Content",
    500: "Internal Server Error",
    503: "Service Unavailable",
}

# What a refusal is served as: RFC 9457's own media type, so a client
# can tell a refusal this API wrote from a page a proxy in front of it
# did without reading either.
PROBLEM_MEDIA_TYPE = "application/problem+json"

# Where the document says the shape of one is.
PROBLEM_SCHEMA = {"$ref": "#/components/schemas/Problem"}


def problem_response(
    status: int,
    detail: str,
    errors: Sequence[FieldProblem] = (),
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """One refusal, as bytes. The only place in this application a
    refusal becomes any.

    Five things can refuse a request here: a repository refusal reaching
    its handler, the gate in front of routing, the last-resort
    middleware, the sanitized replacement for the framework's body
    validation, and the framework's own routing. Each of them used to
    write a body, which is one shape held in five places and five
    chances for it to drift. They call this instead, so a handler knows
    what it is refusing and nothing about what a refusal looks like.

    Built through the `Problem` model rather than as a dictionary, so
    the shape the document declares and the shape the wire carries are
    one declaration. `errors` is empty by default and stays a list in
    the body either way: a member that disappears when it holds nothing
    is a third state a client has to handle.

    `headers` is for the protocol headers a status needs to be itself:
    `WWW-Authenticate` on a 401, `Allow` on a 405. Nothing
    request-derived belongs in it.
    """
    body = Problem(
        title=PROBLEM_TITLES[status],
        status=status,
        detail=detail,
        errors=[FieldError(path=problem.path, message=problem.message) for problem in errors],
    )
    return JSONResponse(
        body.model_dump(),
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=dict(headers) if headers is not None else None,
    )

# What a prompt read answers for an agent this server did not load. The
# name is not quoted back: it arrived in the path, and what is worth
# saying about it is where to look instead.
UNLOADED_AGENT = (
    "this server has not loaded an agent of that name. The agents a server can serve "
    "are the ones it started with, so one written since is served by the restart that "
    "loads it, and one that never existed is a name nothing answers to. "
    "`vinga-server config list` shows the agents that are stored."
)

UNLOADED_AGENT_DESCRIPTION = (
    "No agent of that name was loaded when this server started. An agent written "
    "since then waits for the restart that loads it."
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
    "it composes into one this server cannot serve yet (a prompt fragment deleted while "
    "something still restart-bound names it), a credential stored in it will not open "
    "under the configured keys, or a server it names could not be built (an environment "
    "reference nothing sets, an entry `server.local_only` forbids). Nothing was swapped, "
    "stopped or started, and the running server is exactly as it was. The `detail` is "
    "fixed and names no location, which this refusal shares with the comparison read "
    "beside it and with nothing else in this API: what was refused is arbitrary stored "
    "state, and a sentence composed over it can quote a value that was written into the "
    "wrong field. A server started from this store refuses on the same state and names "
    "the location it refused on."
)

# And its 409, which is neither of the two the shared sentence covers on
# this route: one apply at a time is this endpoint's own exclusion, and
# the snapshot-mode refusal is the one 409 in this API that retrying
# will not clear.
RELOAD_HELD_DESCRIPTION = (
    "Either an apply of this server's configuration is already running, or the "
    "configuration database's write lock is held by another request, or this server "
    "serves a configuration that no store describes and so has nothing to apply. "
    "Nothing was changed. The first two clear on their own and the request can be made "
    "again; the third is what this server is, and only starting one from a store "
    "changes it."
)

# The diff read's three refusals that cannot inherit a shared sentence.
# It addresses nothing and carries no body, so the shared 422 (a stage
# that is not a stage, a MAC that is not one) cannot be what one of its
# own means; its 409 is not one of the three things the shared sentence
# lists; and the shared 503 says the reads in this namespace answer
# emptily, which is exactly what this one must not do.
DIFF_REFUSED_DESCRIPTION = (
    "The stored half was refused: it does not compose into a valid snapshot, or a "
    "credential stored in it will not decrypt under the configured keys. The `detail` "
    "is fixed and names nothing, which this refusal shares with the reload beside it "
    "and with nothing else this API answers with: what was refused is stored state, the "
    "sentence for it would be composed over that state, and this read's answers are "
    "entity names and labels only. A server started from this store refuses on the same "
    "state and names the location it refused on. Nothing was compared here and nothing "
    "was changed."
)

DIFF_MOVED_DESCRIPTION = (
    "Either the configuration database's write lock is held by another request, the "
    "running world was replaced by a reload while this read was between its two halves, "
    "or this server serves a configuration that no store describes and so has nothing "
    "to compare. An answer built across a reload would describe two states that never "
    "existed together, so it is refused instead. Nothing was changed. The first two "
    "clear on their own and the request can be made again; the third is what this "
    "server is, and only starting one from a store changes it."
)

NO_RUNTIME_DIFF_DESCRIPTION = (
    "This application has no running server around it, so there is no world to compare "
    "the database with. Unlike the MCP status read beside it, there is no honest empty "
    "answer: an empty diff would say that everything stored is already in effect."
)

# And what the caller is told, which is not the shared sentence: that
# one says the reads in this namespace answer emptily, and this read
# refuses precisely because an empty answer would be a claim about a
# server that is not there. A document that describes one thing and a
# body that says another leave the reader to decide which to believe.
NO_RUNTIME_DIFF = (
    "this API has no running server around it, so there is nothing to compare the "
    "stored configuration with. An empty diff is not the honest answer here, since it "
    "would say that everything stored is already in effect. A deployment reaches this "
    "read on its server's own port."
)

# And what both surfaces that span the two sides say when there is only
# one side. A server composed from a configuration handed to it serves a
# world no store describes, so the database beside it holds whatever has
# been written since and nothing about what is running: comparing the
# two would report the whole world pending, and applying it would
# install a domain half that describes some other server. Fixed, and
# said once here because the two refusals are the same fact met from
# two directions.
NO_STORED_WORLD = (
    "this server serves a configuration it was given rather than one it read from a "
    "store, so no stored configuration describes what it is running. The database in "
    "its directory holds whatever has been written to it since, which is not this "
    "server's world: comparing the two would report everything as pending, and applying "
    "it would replace what is running with a description of some other server. Nothing "
    "was changed, and making the request again will not help; a server started from a "
    "store answers both of these."
)


@dataclass
class StoreHandle:
    """The one engine a process opens for the configuration database, and
    the keys derived beside it.

    Both are process-wide rather than per-request (#142): the engine is
    opened and migrated once by whichever lifespan owns this application,
    and `VINGA_MASTER_KEY` is parsed once in the same breath. A request
    wraps them in a `ConfigStore` and disposes nothing, because it opened
    nothing.

    The keys are what a stored credential decrypts under. They are held
    here for exactly as long as the engine they configure, they reach no
    log and no response, and `None` is the legitimate state of a
    deployment whose credentials are all environment references.
    """

    engine: Engine
    keys: MultiFernet | None


@contextlib.contextmanager
def open_store(directory: Path) -> Iterator[StoreHandle]:
    """Open the configuration database in `directory` and hold it.

    `open_database` rather than a bare engine, and deliberately: this is
    the only place the schema is brought up to date on the API's path,
    and an application built over a directory nothing has migrated (a
    fresh deployment whose first act is an API write, which is what the
    integration lane's API-first path is) has to come up with a schema.
    `upgrade_to_head` is idempotent and cheap when the database is
    current, so a server that migrated at boot pays one no-op check.

    A directory held by another writer refuses here, as
    `DatabaseBusyError`, which is a `ConfigError` and therefore part of
    the boot failure taxonomy: a locked database at startup is a boot
    that refused with a sentence, not a traceback.
    """
    engine = open_database(directory)
    try:
        yield StoreHandle(engine, load_keys())
    finally:
        engine.dispose()


@contextlib.contextmanager
def installed(runtime: "ApiRuntime", handle: StoreHandle) -> Iterator[None]:
    """Hold `handle` on `runtime` for the length of this block.

    Both owners of the engine install it through here, so that both let
    go of it the same way. Letting go is the half worth stating:
    `Engine.dispose()` replaces an engine's connection pool rather than
    closing the engine down, so an engine reached after disposal opens
    fresh connections quite happily. A handle left on the runtime after
    teardown would therefore let a late request open connections that no
    lifespan owns and nothing will ever close. Cleared, that request
    meets `store_dependency`'s refusal instead, which is the honest
    answer for an application that is no longer being served.

    The mounted owner registers this on its exit stack after the open
    and therefore unwinds it before the disposal, which is the order the
    `with` below has for free.
    """
    runtime.store = handle
    try:
        yield
    finally:
        runtime.store = None


def engine_lifespan(runtime: "ApiRuntime", directory: Path) -> Lifespan[FastAPI]:
    """The standalone owner of the engine: this application's own
    lifespan.

    There are two ways this application runs and therefore two possible
    owners, never both at once. Mounted on a server, the parent lifespan
    opens the engine and installs it here, because Starlette runs no
    lifespan for a mounted application. Run as the top-level application
    (which is what the configuration API's own suites do), this is what
    opens it.

    The application argument is ignored: what this installs into is the
    runtime object it was built beside, so a test that mounts this
    application on a host of its own and lends it this lifespan installs
    into the right place.
    """

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        with open_store(directory) as handle, installed(runtime, handle):
            yield

    return lifespan


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

    `store` is the configuration database's one engine, installed by
    whichever lifespan owns it and None until then; `conversations` is
    still a per-request open, which is a property that store documents
    for itself. The other six are the live objects the server shares
    with this application, or the honest empties an application built
    without a server around it gets.
    """

    store: StoreHandle | None
    conversations: Callable[[], Iterator[Connection]]
    loaded_agents: frozenset[str]
    pending: PendingDevices
    mcp_servers: McpStatusSource | None
    reload: ConfigReloader | None
    agent_prompt: Callable[[str], Awaitable[Any]] | None
    config_diff: ConfigDiffReader | None
    snapshot_only: bool = False


def build_api(
    token: str,
    database_dir: Path,
    loaded_agents: Collection[str] = (),
    pending: PendingDevices | None = None,
    mcp_servers: McpStatusSource | None = None,
    reload: ConfigReloader | None = None,
    agent_prompt: Callable[[str], Awaitable[Any]] | None = None,
    config_diff: ConfigDiffReader | None = None,
    snapshot_only: bool = False,
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

    `reload` applies the stored configuration to the running server: the
    world new work binds and the MCP managers that serve it. A callable
    rather than the pieces it needs, because what it closes over is the
    composition root's business: the generation holder, the registry,
    and where the blocking half of the re-read runs. None is the honest
    answer for an application without a server, and the route refuses
    with 503 rather than pretending to have applied something.

    `snapshot_only` says that the server around this application was
    composed from a configuration handed to it rather than read from a
    store. False is the ordinary deployment and the default, because an
    application built without a server around it is not in that mode
    either: it has no server at all, which the fields above already say.
    What the mode changes is the three surfaces that would otherwise
    describe a store that describes nothing this server serves: the diff
    and the reload refuse, and a device or default-agent write says that
    what it wrote takes effect when a server boots from this store.

    `agent_prompt` assembles the prompt a session opening now as one
    agent would be sent, and answers None for an agent this server did
    not load. A callable for the reason the reload is one: what it
    closes over is the composition root's business again (the loaded
    configuration, the MCP registry and the memory store), and this
    application must not learn what a prompt is made of. None is the
    honest answer without a server, and the route answers 503.

    `config_diff` compares what the database holds with what the server
    around this application is serving. A callable for the reason the
    two above are: what it closes over is the world this process booted,
    the credentials loaded with it and the registry a reload replaces,
    and this application must not learn which kind of configuration
    converges where. None is the honest answer without a server, and the
    route answers 503 rather than reporting that nothing is pending.
    """
    runtime = build_api_runtime(
        database_dir,
        loaded_agents,
        pending,
        mcp_servers,
        reload,
        agent_prompt,
        config_diff,
        snapshot_only,
    )
    # A lifespan of its own, which runs only when this application is the
    # top-level one: it opens the configuration database and installs the
    # handle on the runtime above. Mounted on a server, Starlette runs no
    # lifespan here and the parent's does the installing instead, so the
    # engine has exactly one owner either way (#142).
    api = _application(engine_lifespan(runtime, database_dir))
    # Attached rather than closed over: the read and write routes take
    # the pieces of it with Depends(...), and milestone 1 had none of
    # them yet. One object rather than one attribute each, so what a
    # route resolves is a field of a declared type.
    api.state.api_runtime = runtime
    # Added last is outermost, so a failure inside the gate itself
    # answers as sanitized as one inside a handler.
    api.add_middleware(_BearerGate, token=token)
    api.add_middleware(_SanitizedErrors)
    return api


def build_api_runtime(
    database_dir: Path,
    loaded_agents: Collection[str] = (),
    pending: PendingDevices | None = None,
    mcp_servers: McpStatusSource | None = None,
    reload: ConfigReloader | None = None,
    agent_prompt: Callable[[str], Awaitable[Any]] | None = None,
    config_diff: ConfigDiffReader | None = None,
    snapshot_only: bool = False,
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

    The store handle is not among them. It is the one field a lifespan
    installs rather than a builder fills, because it is the one field
    that is an open resource: whoever opens it is whoever will dispose
    it, and neither of those is this function.
    """
    return ApiRuntime(
        store=None,
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
        reload=reload,
        agent_prompt=agent_prompt,
        config_diff=config_diff,
        snapshot_only=snapshot_only,
    )


def _empty_pending() -> PendingDevices:
    """A table for an application built without a server around it."""
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


NO_ENGINE = (
    "the configuration API has no database engine: its lifespan was never entered. "
    "Mounted on a server, the parent lifespan installs one; standalone, this "
    "application's own does."
)


def store_dependency(runtime: ApiRuntime) -> Iterator[ConfigStore]:
    """The repository, for the length of one request, over the engine
    this process already owns.

    It used to open and migrate a database of its own on every request
    and re-parse `VINGA_MASTER_KEY` with it. Both are now done once, by
    the lifespan that owns them (#142), and what is left here is the
    wrapping: a `ConfigStore` is a view over an engine and holds nothing
    to release, so there is nothing to dispose on the way out.

    A missing handle is a programming error, not a state to recover
    from, and it raises rather than quietly opening a second engine: an
    application whose lifespan never ran is one nobody may serve
    requests from, and the honest answer is to say so at the first
    request rather than to run on with an owner nothing will dispose.
    `is None` and not a truthiness test, because a handle is an object
    with no falsehood to speak of.
    """
    handle = runtime.store
    if handle is None:
        raise RuntimeError(NO_ENGINE)
    yield ConfigStore(handle.engine, handle.keys)


def _store(request: Request) -> Iterator[ConfigStore]:
    """The repository, for the length of one request.

    Taken from the application rather than closed over by the routes, so
    that the document can be rendered from an application built without
    a database directory: `build_api` attaches the runtime and
    `document()` never resolves it.
    """
    runtime: ApiRuntime = request.app.state.api_runtime
    yield from store_dependency(runtime)


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


def _pending(request: Request) -> PendingDevices:
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


def _reload(request: Request) -> ConfigReloader | None:
    """What applies the stored configuration to the running server, or
    None for an application built without one. Taken from the
    application for the reason the store is."""
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.reload


ReloadDep = Annotated[ConfigReloader | None, Depends(_reload)]


def _snapshot_only(request: Request) -> bool:
    """Whether the server around this application serves a configuration
    that no store describes. Taken from the application for the reason
    the store is, and beside `loaded_agents` because it is the same kind
    of fact: something about the server that a write's acknowledgement
    and a runtime action both have to know."""
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.snapshot_only


SnapshotOnlyDep = Annotated[bool, Depends(_snapshot_only)]


def _agent_prompt(request: Request) -> Callable[[str], Awaitable[Any]] | None:
    """What assembles one agent's prompt from the running server, or
    None for an application built without one. Taken from the
    application for the reason the store is."""
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.agent_prompt


AgentPromptDep = Annotated[Any, Depends(_agent_prompt)]


def _config_diff(request: Request) -> ConfigDiffReader | None:
    """What compares the stored configuration with the running server,
    or None for an application built without one. Taken from the
    application for the reason the store is."""
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.config_diff


ConfigDiffDep = Annotated[ConfigDiffReader | None, Depends(_config_diff)]


def _pending_view(device: PendingRecord) -> dict[str, Any]:
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

    The content is written out rather than declared as `model: Problem`.
    A model makes FastAPI generate `application/json` for the response
    and then deep-merge whatever else is asked for beside it, which
    would leave every refusal advertising a media type this API does not
    send. The schema is a reference into `components`, where the
    `Problem` schema is injected below, which is the same seam the write
    routes' request bodies use.
    """
    described = {**PROBLEM_DESCRIPTIONS, **(instead or {})}
    return {
        status: {
            "description": described[status],
            "content": {PROBLEM_MEDIA_TYPE: {"schema": PROBLEM_SCHEMA}},
        }
        for status in statuses
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

# The five commanded kinds, held by name because a route reads facts of
# its own kind: the model a fragment of it is validated against, the
# slot kind a credential on it hangs under, and when a write of it takes
# effect. What the route knows without asking is the rest: its path, its
# name, its prose, and which repository call it makes.
_PROVIDER = entities.descriptor("provider")
_MCP_SERVER = entities.descriptor("mcp-server")
_PROMPT_FRAGMENT = entities.descriptor("prompt-fragment")
_AGENT = entities.descriptor("agent")
_AGENT_DEFAULTS = entities.descriptor("agent-defaults")


def _slot(descriptor: entities.EntityDescriptor, identity: str, slot: str) -> SecretLocation:
    """Where one stored credential lives: the kind a secret hangs on,
    the entity as a secret location spells it (the dotted join of the
    parameters that address it), and the slot inside that entry.

    The kind is read off the descriptor rather than written at the four
    routes that address a credential, because which kinds can hold one
    is a decision `secrets.EntityKind` and the registry already state
    together.
    """
    return SecretLocation(
        kind=descriptor.secret_slots,  # type: ignore[arg-type]
        identity=identity,
        slot=slot,
    )


def _entity_reads(api: FastAPI) -> None:
    """Every commanded kind's collection and entry reads.

    Written out, one function per route, rather than built from the
    registry. What a route is called, what its docstring says, what it
    answers and what it can refuse are the committed OpenAPI document's
    bytes, and the factory this replaces carried all four as descriptor
    data so that it could set `__name__`, `__doc__` and `__signature__`
    back onto a generated function. The document is the contract, its
    drift check is in CI and in the suite, and that diff is the whole
    proof that the two spellings describe one API.

    Registered in the order the document has, kind by kind: the paths
    appear as they are first registered, and the operations under a path
    in the order they were added to it.

    Nothing here decides anything about the configuration, exactly as
    nothing did in the factory: which entry exists is the repository's
    decision, what a read may show is the view's, and what is left is
    the path, the status code and the shape. Plain `def`, so FastAPI
    runs them on the threadpool and the synchronous repository never
    blocks the event loop.
    """

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
        "/mcp-servers",
        response_model=dict[str, Envelope],
        responses=_problems(401, 409, 500),
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

    @api.get(
        "/prompt-fragments",
        response_model=dict[str, Envelope],
        responses=_problems(401, 409, 500),
    )
    def read_prompt_fragments(store: StoreDep) -> dict[str, Any]:
        """Every shared prompt fragment, by name."""
        return views.prompt_fragments(store.load())

    @api.get(
        "/prompt-fragments/{name}",
        response_model=Envelope,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def read_prompt_fragment(name: str, store: StoreDep) -> dict[str, Any]:
        """One shared prompt fragment, with its text as it was written:
        it is what the model is given, and there is nothing in it to
        mask."""
        return views.prompt_fragment(store.read_prompt_fragment(name))

    @api.get(
        "/agents",
        response_model=dict[str, Envelope],
        responses=_problems(401, 409, 500),
    )
    def read_agents(store: StoreDep) -> dict[str, Any]:
        """Every agent, by name."""
        return views.agents(store.load())

    @api.get(
        "/agents/{name}",
        response_model=Envelope,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def read_agent(name: str, store: StoreDep) -> dict[str, Any]:
        """One agent."""
        return views.agent(store.read_agent(name))

    @api.get(
        "/agent-defaults",
        response_model=Envelope,
        responses=_problems(401, 409, 500),
    )
    def read_agent_defaults(store: StoreDep) -> dict[str, Any]:
        """What every agent uses unless it names something else. One
        entry for the whole deployment, and never missing: an unwritten
        one reads as the empty entry."""
        return views.agent_defaults(store.read_agent_defaults())


def _reads(api: FastAPI) -> None:
    """Every read the API serves.

    Each handler is one repository call and one view, and restates
    nothing: which entity exists is the repository's decision, what a
    read may show is the view's, and what is left here is the path, the
    status code and the shape. They are plain `def`, so FastAPI runs
    them on the threadpool and the synchronous repository never blocks
    the event loop.

    The reads of the commanded kinds are written out just above; the
    ones written here are the reads the tiers do not describe. The
    whole configuration is every kind at once. A
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

    # Every commanded kind's collection and entry reads, written out
    # above because the document's bytes are their prose.
    _entity_reads(api)

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
        "/runtime/config/reload",
        response_model=ConfigReloadResult,
        responses=_problems(
            401,
            409,
            422,
            500,
            503,
            instead={
                409: RELOAD_HELD_DESCRIPTION,
                422: RELOAD_REFUSED_DESCRIPTION,
            },
        ),
    )
    async def reload_config(
        servers: McpServersDep, reload: ReloadDep, snapshot_only: SnapshotOnlyDep
    ) -> ConfigReloadResult:
        """Apply the stored configuration to this running server.

        The one action in this namespace, and the one way a stored
        change reaches a running server without a restart. What it
        applies is every kind this server can apply while it runs, which
        today is the MCP entries with the secrets stored on them and the
        agents' effective `mcp` grant lists, the shared prompt
        fragments, and each agent's own prompt text and includes.
        Everything else still waits for the start that reads it: the
        providers, the agent set, `agent_defaults`, and the whole server
        section, which is this process's own file and is never re-read.

        Nothing is swapped, stopped or started until the whole new world
        has been composed, validated and built, so a refusal (422) has
        changed nothing at all. A server that merely will not connect is
        not a refusal: it applies, and says `down` with its reason under
        `mcp.servers`, and is reconnected in the background when a
        session that would use it opens.

        Live conversations are not dropped, and when one meets the
        result depends on which half moved. The tools an agent may reach
        are snapshotted per reply, so a session picks those up on its
        next utterance; a call in flight on a server this stopped fails
        into the same error result a server dropping mid-call produces.
        Prompt text is assembled at an activation and cached for it, so
        an agent's own prompt, a fragment it includes and an entry's
        `instructions` alike reach a conversation at its
        next activation, which is a new session or an agent switch, and
        never a reply of one already running. Such an entry is reported `unchanged`
        under `mcp`, which is a statement about its connection: the
        connection is what did not change.

        The world arrives in two steps, which is what makes the sentence
        above per half rather than per apply: what a new activation
        assembles from is put in place first, and the MCP managers are
        stopped, started and swapped after it, so a session activating
        between the two sees the new prompts with the old tool world for
        at most one utterance. Nothing else can interleave, because one
        apply runs at a time.

        One apply runs at a time: a concurrent one is refused with 409
        and has changed nothing, like a write that could not take the
        database's lock. A server serving a configuration that no store
        describes refuses with 409 as well, and says that starting from
        a store is what changes it.
        """
        # This docstring is the endpoint's description in the committed
        # document, so what belongs to the handler rather than to the
        # contract is said here. What the reload did and what is running
        # afterwards arrive together, composed where the phases are:
        # taking them apart here would put an invariant of the apply's
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
        if snapshot_only:
            raise SnapshotOnlyError(NO_STORED_WORLD)
        return await reload()

    @api.get(
        "/runtime/config/diff",
        response_model=ConfigDiff,
        responses=_problems(
            401,
            409,
            422,
            500,
            503,
            instead={
                409: DIFF_MOVED_DESCRIPTION,
                422: DIFF_REFUSED_DESCRIPTION,
                503: NO_RUNTIME_DIFF_DESCRIPTION,
            },
        ),
    )
    async def read_config_diff(
        diff: ConfigDiffDep, snapshot_only: SnapshotOnlyDep
    ) -> ConfigDiff:
        """What the database holds that this server is not serving, kind
        by kind, with the boundary each kind's changes converge at.

        The read the other two in this namespace cannot give: they say
        what is running, the entity reads say what is stored, and the
        question an operator actually has after a write is what stands
        between the two. Most of the configuration is a boot-time
        snapshot, so most of an answer here is waiting for a restart;
        the MCP entries and the agents' grants are waiting for the
        reload below; device bindings and the default agent are read as
        a device asks and are therefore never pending, so they carry
        their label and no lists.

        Names and labels, and nothing else. No entity bodies, no values,
        no masks and no secret marks cross this surface: what a
        credential change looks like here is the entity holding it
        listed as changed.

        The stored half is the re-read the reload begins with, so a
        stored configuration that will not compose, or a credential in
        it that will not decrypt, is refused here under the status it
        would be refused under there. That equivalence runs one way
        only: this compares configuration and connects nothing, while a
        reload goes on to compose the world it would serve and to build
        a server per referenced entry, and can still refuse on either,
        so an answer here says what has not been applied and never that
        applying it would succeed.
        The running half is read on the loop that owns it, either side
        of that database read: a reload landing in between would leave
        the two halves describing states that never existed together, so
        such an answer is not composed at all and the request is refused
        as retryable instead.
        """
        # The docstring is the endpoint's description in the committed
        # document, so what belongs to the handler rather than to the
        # contract is said here. The whole comparison arrives composed,
        # for the reason the reload's whole result does: which world is
        # compared against which, and what makes the two one world, are
        # decided where both are in hand, and a handler that took the
        # answer apart would be holding an invariant it cannot keep.
        if diff is None:
            raise NoRuntimeError(NO_RUNTIME_DIFF)
        if snapshot_only:
            raise SnapshotOnlyError(NO_STORED_WORLD)
        return await diff()


def _entity_writes(api: FastAPI) -> None:
    """Every commanded kind's writes, deletes and credential slots.

    Written out for the reason the reads above are, and in the order the
    document has: per kind, the entity write, its delete, and then the
    two credential routes for the two kinds that can hold one.

    A handler makes one repository call and answers with what it did and
    when it applies. The sentence is `writes.py`'s, so the API and the
    CLI's break-glass path cannot come to describe one act differently,
    and the timing is the kind's own `notice`, which is a descriptor
    fact because it is about what was written rather than about the
    route that wrote it.

    A fragment is handed to the repository unread (`RawBody`), which is
    the rule the module docstring gives: FastAPI's own validation echoes
    what it rejected, and a fragment can carry a pasted credential.
    """

    @api.put(
        "/providers/{stage}/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=_request_body(_PROVIDER.model),
    )
    def write_provider(
        stage: str, name: str, body: RawBody, store: StoreDep
    ) -> dict[str, Any]:
        """Create or replace one provider from a fragment in the shape
        the YAML section had. The running server builds it again at the
        next reload, and the conversations that open after that speak
        through the new one."""
        store.set_provider(stage, name, body)
        return _acknowledge(wrote_provider(stage, name), _PROVIDER.notice)

    @api.delete(
        "/providers/{stage}/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_provider(stage: str, name: str, store: StoreDep) -> dict[str, Any]:
        """Delete one provider, and the secrets stored on it. Refused
        while an agent or the agent defaults still name it."""
        store.delete_provider(stage, name)
        return _acknowledge(deleted_provider(stage, name), _PROVIDER.notice)

    @api.put(
        "/providers/{stage}/{name}/secrets/{slot}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
        openapi_extra=_request_body(SecretValue),
    )
    def write_provider_secret(
        stage: str, name: str, slot: str, body: RawBody, store: StoreDep
    ) -> dict[str, Any]:
        """Store one of this provider's credentials, encrypted. The slot
        is the option name the credential fills, such as api_key; a
        stored secret takes precedence over an environment reference
        written for the same slot. A credential is read as the provider
        is built, and the next reload builds it again."""
        location = _slot(_PROVIDER, provider_identity(stage, name), slot)
        store.set_secret(location, _secret(body))
        return _acknowledge(wrote_secret(location.describe()), _PROVIDER.notice)

    @api.delete(
        "/providers/{stage}/{name}/secrets/{slot}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_provider_secret(
        stage: str, name: str, slot: str, store: StoreDep
    ) -> dict[str, Any]:
        """Remove one stored credential. A slot holding none is a 404."""
        location = _slot(_PROVIDER, provider_identity(stage, name), slot)
        store.clear_secret(location)
        return _acknowledge(cleared_secret(location.describe()), _PROVIDER.notice)

    @api.put(
        "/mcp-servers/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=_request_body(_MCP_SERVER.model),
    )
    def write_mcp_server(name: str, body: RawBody, store: StoreDep) -> dict[str, Any]:
        """Create or replace one MCP server. The running server applies
        it at the next reload, with no restart."""
        store.set_mcp_server(name, body)
        return _acknowledge(wrote_mcp_server(name), _MCP_SERVER.notice)

    @api.delete(
        "/mcp-servers/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_mcp_server(name: str, store: StoreDep) -> dict[str, Any]:
        """Delete one MCP server, and the secrets stored on it. The
        running server stops it at the next reload."""
        store.delete_mcp_server(name)
        return _acknowledge(deleted_mcp_server(name), _MCP_SERVER.notice)

    @api.put(
        "/mcp-servers/{name}/secrets/{slot}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
        openapi_extra=_request_body(SecretValue),
    )
    def write_mcp_secret(
        name: str, slot: str, body: RawBody, store: StoreDep
    ) -> dict[str, Any]:
        """Store one of this MCP server's credentials, encrypted. The
        slot is `env.<KEY>` or `headers.<Key>`, which is where the value
        would otherwise have been written as a $VAR reference.

        Rotation is exactly what the ciphertext half of the reload's
        diff applies, so this carries the reload notice too: the entry
        is rebuilt with the fresh credential and reconnected."""
        location = _slot(_MCP_SERVER, name, slot)
        store.set_secret(location, _secret(body))
        return _acknowledge(wrote_secret(location.describe()), _MCP_SERVER.notice)

    @api.delete(
        "/mcp-servers/{name}/secrets/{slot}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_mcp_secret(name: str, slot: str, store: StoreDep) -> dict[str, Any]:
        """Remove one stored credential, which the next reload applies
        by rebuilding the entry without it."""
        location = _slot(_MCP_SERVER, name, slot)
        store.clear_secret(location)
        return _acknowledge(cleared_secret(location.describe()), _MCP_SERVER.notice)

    @api.put(
        "/prompt-fragments/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=_request_body(_PROMPT_FRAGMENT.model),
    )
    def write_prompt_fragment(
        name: str, body: RawBody, store: StoreDep
    ) -> dict[str, Any]:
        """Create or replace one shared prompt fragment.

        It carries the reload sentence: a fragment is prompt text, the
        reload re-reads the whole `prompt_fragments` kind, and prompt
        text is assembled at an activation, so the new words reach every
        agent that includes this fragment at that agent's next
        activation. Which agents those are depends on the layer that
        names it: an agent's own `prompt_includes` is applied by the
        same reload, while `agent_defaults.prompt_includes` is what
        every agent's effective value is inherited through and waits for
        the next server start."""
        store.set_prompt_fragment(name, body)
        return _acknowledge(wrote_prompt_fragment(name), _PROMPT_FRAGMENT.notice)

    @api.delete(
        "/prompt-fragments/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_prompt_fragment(name: str, store: StoreDep) -> dict[str, Any]:
        """Delete one shared prompt fragment. Refused while any layer
        still includes it."""
        store.delete_prompt_fragment(name)
        return _acknowledge(deleted_prompt_fragment(name), _PROMPT_FRAGMENT.notice)

    @api.put(
        "/agents/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=_request_body(_AGENT.model),
    )
    def write_agent(name: str, body: RawBody, store: StoreDep) -> dict[str, Any]:
        """Create or replace one agent. Every provider and MCP server it
        names has to exist already, which is what the natural creation
        order is about."""
        store.set_agent(name, body)
        return _acknowledge(wrote_agent(name), _AGENT.notice)

    @api.delete(
        "/agents/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_agent(name: str, store: StoreDep) -> dict[str, Any]:
        """Delete one agent. Refused while a device binding or the
        default agent still names it."""
        store.delete_agent(name)
        return _acknowledge(deleted_agent(name), _AGENT.notice)

    @api.put(
        "/agent-defaults",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=_request_body(_AGENT_DEFAULTS.model),
    )
    def write_agent_defaults(body: RawBody, store: StoreDep) -> dict[str, Any]:
        """Replace what every agent uses unless it names something else.
        One entry for the whole deployment, so this is a replace and
        there is nothing to delete."""
        store.set_agent_defaults(body)
        return _acknowledge(wrote_agent_defaults(), _AGENT_DEFAULTS.notice)


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

    The writes of the commanded kinds are written out just above, like
    their reads. What is written here is what those six verbs do
    not describe: binding a device by its MAC or by the code it is
    showing, and setting or clearing the default agent, each with its
    own verb, its own argument-shaped body, and a notice that depends on
    whether the agent it names was loaded.
    """

    # Every commanded kind's writes, deletes and credential slots,
    # written out just above in the order the document lists them.
    _entity_writes(api)

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
        snapshot_only: SnapshotOnlyDep,
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
            binding_notice(_unloaded(bound.agents, loaded), snapshot_only),
        )

    @api.put(
        "/devices/{mac}",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=_request_body(DeviceBinding),
    )
    def write_device(
        mac: str,
        body: RawBody,
        store: StoreDep,
        loaded: LoadedAgentsDep,
        pending: PendingDep,
        snapshot_only: SnapshotOnlyDep,
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
            binding_notice(_unloaded(bound.agents, loaded), snapshot_only),
        )

    @api.delete(
        "/devices/{mac}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_device(
        mac: str, store: StoreDep, snapshot_only: SnapshotOnlyDep
    ) -> dict[str, str]:
        """Remove one device's binding, which with no default agent set
        means the device is refused at the handshake. Live, with no
        agent to be loaded or not: the device stops being served at its
        next check-in, though a conversation already running is left to
        finish."""
        return _acknowledge(
            deleted_device(store.delete_device(mac)), binding_notice(snapshot_only=snapshot_only)
        )

    @api.put(
        "/default-agent",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=_request_body(DefaultAgentName),
    )
    def write_default_agent(
        body: RawBody,
        store: StoreDep,
        loaded: LoadedAgentsDep,
        pending: PendingDep,
        snapshot_only: SnapshotOnlyDep,
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
            wrote_default_agent(name),
            binding_notice(_unloaded([name], loaded), snapshot_only),
        )

    @api.delete(
        "/default-agent",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 500),
    )
    def remove_default_agent(
        store: StoreDep, snapshot_only: SnapshotOnlyDep
    ) -> dict[str, str]:
        """Unset it, leaving the devices map as the allowlist.
        Idempotent, like the CLI: there is no such thing as a default
        agent that was already not set. Live, like the delete above:
        the next unbound device to ask is turned away."""
        store.clear_default_agent()
        return _acknowledge(
            CLEARED_DEFAULT_AGENT, binding_notice(snapshot_only=snapshot_only)
        )


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
        response = problem_response(
            401, UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"}
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
            failure = exc
            events.emit(lambda: ApiError(failure=ClassName.of(failure)))
            if started:
                # Half a response is already on the wire, so there is
                # nothing left to say that would not corrupt it. Ended
                # here rather than re-raised: re-raising only reaches an
                # outer logger, which would write the traceback this
                # just took care not to.
                return
            await problem_response(500, UNEXPECTED)(scope, receive, send)


def _application(lifespan: Lifespan[FastAPI] | None = None) -> FastAPI:
    """The sub-application without its gate: what the server mounts and
    what the document is rendered from, so the two cannot disagree about
    the routes.

    `lifespan` is how the standalone path owns its engine, and is None
    for the document, which is rendered from an application nobody
    serves and which therefore opens nothing.
    """
    api = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        lifespan=lifespan,
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
    # The fifth emitter, which is the framework itself: an unmatched
    # path and an unsupported method are refusals this application never
    # wrote a line of, and without this they leave in a body of
    # Starlette's own.
    api.add_exception_handler(StarletteHTTPException, _routing_refusal)
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
    vocabulary whichever way they reached it, and the fields it names
    travel beside it where the refusal named any."""

    async def handler(request: Request, exc: Exception) -> JSONResponse:
        if status >= 500:
            # One fixed line naming the exception's class, and never the
            # exception itself: a record's args hold whatever is passed
            # to them, and an exception object carries its message and
            # its chain to anything that walks it. The sentence goes to
            # the caller, which is the channel that was sanitized for
            # it.
            failure = exc
            events.emit(lambda: ApiStorageError(failure=ClassName.of(failure)))
        # Every type this handler is registered for is a ConfigError, so
        # the check is about typing rather than about doubt; an
        # exception that arrived some other way has nothing structured
        # to offer and says so.
        problems = exc.problems if isinstance(exc, ConfigError) else ()
        return problem_response(status, str(exc), problems)

    return handler


async def _malformed_request(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI's own validation, sanitized. Its default 422 echoes the
    rejected input back per error, and a configuration fragment can
    carry a pasted credential, so the whole body is replaced by a
    sentence describing the expectation.

    No `errors` either, for the same reason: the fields it would name
    are the ones it read out of the body it refused to read."""
    return problem_response(422, MALFORMED_REQUEST)


async def _routing_refusal(request: Request, exc: Exception) -> JSONResponse:
    """The framework's own refusals, in this API's shape.

    Without this, an authenticated request to an unmatched path, or to a
    route with the wrong method, is answered by Starlette in a body
    nothing else here sends, which makes "every refusal is one shape" a
    claim with a hole in it. What it renders is the exception's detail,
    which for a routing 404 and a 405 is a fixed phrase of Starlette's
    (`Not Found`, `Method Not Allowed`) and never anything the request
    carried; the tests assert that rather than assume it.

    The exception's headers are kept, because a 405 without its `Allow`
    is not a 405. They are the framework's own and are not built from
    the request.
    """
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - registered by type
        raise exc
    return problem_response(exc.status_code, str(exc.detail), headers=exc.headers)


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
    for model in ENTITY_MODELS + REQUEST_MODELS + PROBLEM_MODELS:
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
    "ConfigDiff",
    "ConfigDocument",
    "ConfigReloadResult",
    "DefaultAgent",
    "DefaultAgentName",
    "DeviceBinding",
    "Envelope",
    "FieldError",
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
