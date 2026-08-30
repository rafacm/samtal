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
store's reads (`/sessions`) are registered from `conversations/api.py`,
where their route functions and their response models live, because
what they answer is that store's business and not this one's. The
namespace is `sessions` and the store is `conversations` deliberately:
what these three reads answer is one connection episode and the turns
inside it, and the thread that spans several of them is a different
entity with a namespace of its own. They are registered on this application so that the gate,
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
import json
import logging
import os
import re
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
from fastapi import Body, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Connection, Engine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Route
from starlette.types import ASGIApp, Lifespan, Message, Receive, Scope, Send

from vinga_server.config import entities, views
from vinga_server.config.docgen import API_OPTIONS_NOTE
from vinga_server.config.entities import (
    BINDING_NOTICE,
    BINDING_UNSERVED_NOTICE,
    RESTART_NOTICE,
    SNAPSHOT_NOTICE,
)
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
    DatabaseConfig,
    DomainConfig,
    FieldProblem,
    normalize_mac,
)
from vinga_server.config.provider_options import component_name, declared_options
from vinga_server.config.responses import (
    EVENT_STREAM_MEDIA_TYPE,
    PROBLEM_MEDIA_TYPE,
    PROBLEM_TITLES,
    Acknowledgement,
    AppliedDocument,
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
    MemoryCorrection,
    MemoryStateKey,
    PendingDevice,
    Problem,
    PromptBlock,
    RuntimeInfo,
    SecretSlot,
    SecretValue,
    ServableAgents,
    StoredSecretLocation,
    request_body,
)
from vinga_server.config.secrets import MASK, SecretLocation, load_keys, provider_identity
from vinga_server.config.store import Applied, ConfigStore
from vinga_server.conversations import api as conversations
from vinga_server.db import open_database
from vinga_server.events import ServerEvents
from vinga_server.events.catalog import ApiError, ApiStorageError
from vinga_server.events.live import (
    DEFAULT_LEVEL,
    LEVELS,
    Dropped,
    Filters,
    LiveEvents,
    Streamed,
)
from vinga_server.events.values import ClassName
from vinga_server.memory import api as memory

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

_API_TITLE = "vinga-server configuration API"

# Where this document's prose lives, which is not in this file.
#
# The descriptions below are the document's own text rather than
# anything this module decides, and as literals they ran to some 550
# lines of string in the middle of the transport code. One file per
# description under `api_descriptions/`, read at import. A route's own
# prose stays a docstring, deliberately: FastAPI reads a route's
# docstring as its operation description, so the docstring IS the
# description, and a route whose prose lived in another file would be
# harder to read rather than easier.
#
# The files are package data, and nothing run from a checkout can prove
# a wheel carries them: the source tree makes every file readable
# whether it was packaged or not. So CI renders this document from the
# installed wheel with the source tree off sys.path and diffs it against
# the committed copy, which is the same discipline the Alembic scripts
# already get.
_DESCRIPTIONS = Path(__file__).parent / "api_descriptions"

# What a description file may ask to have filled in, and what fills it.
# `$NAME$` sigils rather than `{name}` fields, because the prose carries
# literal path braces (`/devices/pending/{code}`, `/runtime/agents/{name}/prompt`)
# that a format call would read as placeholders; no `$` occurs in the
# prose itself, which is what makes the sigil unambiguous. Filled from
# the same constants the literals interpolated, so the mask the document
# states and the mask a read displays are still one string.
_SUBSTITUTIONS: dict[str, str] = {
    "MASK": MASK,
    "API_OPTIONS_NOTE": API_OPTIONS_NOTE,
}

_SIGIL = re.compile(r"\$([A-Z_]+)\$")


class MissingDescriptionError(RuntimeError):
    """A description file is missing, or names a substitution nothing
    provides.

    Raised at import rather than at the first render, and raised rather
    than skipped: what would be missing is a piece of the committed
    contract, and a document that quietly lost a paragraph is worse than
    an application that refuses to start. A packaging fault is how this
    happens, so the sentence says so.
    """


def _description(name: str) -> str:
    """One description file, with its substitutions filled in.

    The file holds the bytes the document carries, minus the single
    trailing newline a text file ends with: a paragraph break in the
    document is a blank line in the file, and nothing else is
    transformed. The wrapping in the file is the wrapping in the
    contract, which is what keeps the committed document byte-identical
    across this move.
    """
    path = _DESCRIPTIONS / f"{name}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise MissingDescriptionError(
            f"the configuration API document's {name} description is missing from "
            f"{_DESCRIPTIONS}: {error}. The descriptions are package data, so an "
            f"installation without them is a packaging fault and not a deployment's."
        ) from error

    def fill(sigil: re.Match[str]) -> str:
        key = sigil.group(1)
        if key not in _SUBSTITUTIONS:
            raise MissingDescriptionError(
                f"the configuration API document's {name} description names "
                f"${key}$, which nothing fills; what this loader substitutes is "
                f"{sorted(_SUBSTITUTIONS)}."
            )
        return _SUBSTITUTIONS[key]

    return _SIGIL.sub(fill, text.removesuffix("\n"))


_API_DESCRIPTION = _description("api")

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
_UNAUTHORIZED = (
    "this API requires a bearer token: send it as the Authorization header, "
    "`Authorization: Bearer <token>`, where the token is the value of the "
    "environment variable server.api.secret_env names"
)

# The request never gets quoted back, here least of all: a body that
# fails to parse may be a fragment carrying a pasted credential.
_MALFORMED_REQUEST = (
    "the request could not be read in the shape this endpoint expects; send a "
    "JSON object body, and see the committed OpenAPI document for the shape. "
    "The body is never quoted back"
)

# What a caller is told when something in here failed rather than
# something in the request. The log records that it happened and what
# kind of failure it was, and deliberately no more than that.
_UNEXPECTED = "the server failed to handle this request; the failure is recorded in its log"

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

# The bodies that are arguments rather than fragments, as the
# document describes them. The models are documentation and nothing
# else: they are injected into `components` beside the entity models and
# named by the routes' `openapi_extra`, and they are deliberately not
# declared as body types, for the reason the entity models are not
# either. What enforces them at runtime is the exact-shape parser
# further down, which describes the expectation and never echoes what it
# refused.
#
# The last two are the memory namespace's, and they are here rather than
# beside its routes because this is where `components` is assembled: a
# `$ref` a route declares has to resolve into a schema something
# injected. Their parsers are that module's own, written to the same
# rule, because what those two bodies carry is a remembered fact and a
# model-chosen key.
REQUEST_MODELS: tuple[type[BaseModel], ...] = (
    DeviceBinding,
    DefaultAgentName,
    SecretValue,
    MemoryCorrection,
    MemoryStateKey,
)

# And the fourth, which is the whole domain half rather than one entry
# of it: `POST /apply` takes a partial `DomainConfig`, so the model that
# describes the configuration IS the model that describes the body, and
# a second schema written for the request would be a second statement of
# the same shape. Every field of it has a default, which is what makes
# the schema of the whole document the schema of a partial one.
DOCUMENT_MODELS: tuple[type[BaseModel], ...] = (DomainConfig,)

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
_DEVICE_BODY = (
    'the body has to be a JSON object with exactly one key, "agents", holding an '
    "array of agent names as strings. Nothing sent is quoted back"
)

_DEFAULT_AGENT_BODY = (
    'the body has to be a JSON object with exactly one key, "name", holding the '
    "agent's name as a string. Nothing sent is quoted back"
)

_SECRET_BODY = (
    'the body has to be a JSON object with exactly one key, "secret", holding the '
    "credential as a non-empty string. Nothing sent is quoted back, which on this "
    "endpoint is the point"
)

# The two refusals a claim by code can meet. Neither quotes the code
# back: it is what arrived in the path, and what is worth saying about
# it is what the operator should read instead.
_UNKNOWN_CODE = (
    "no device is waiting with that activation code. A code lasts ten minutes and is "
    "retired the moment it is claimed, and a device that has been waiting longer is "
    "already showing a fresh one: read the code currently on the device's screen and "
    "use that. `vinga-server config device pending list` lists the codes this "
    "server is showing right now."
)

_CODE_IN_FLIGHT = (
    "that activation code is being claimed by another request right now. Nothing was "
    "changed; run the command again in a moment, when the code will either have been "
    "bound by that request or be free again."
)

# And what a claim refused by the repository says instead of the
# repository's own sentence, which names the agent it could not resolve.
# This is the one route where an agent name is typed beside an
# activation code rather than read out of stored configuration, so it is
# the one route where a mistake puts something else there.
_CLAIM_REFUSED = (
    "the device showing that code could not be bound: the request's agents name at "
    "least one agent this deployment does not have. Nothing was changed and the code "
    "is still claimable. What was sent is not quoted back; run "
    "`vinga-server config list` to see the agents that exist."
)

# What clearing the default agent says it did. The one acknowledgement
# this file writes that is a sentence rather than a naming of what was
# written, because there is nothing to name: unsetting the default is a
# configuration and not an absence, so the line says what the deployment
# now is. Underscored because its only reader is the route below: a
# public name is what a module offers, and this one offers nothing.
_CLEARED_DEFAULT_AGENT = "default agent cleared; the devices map is now the allowlist"

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

# What each of them is called, and what a refusal is served as, are
# `config/responses.py`'s: the shape of a refusal, the media type it
# travels under and the titles it can carry are one declaration there,
# because the CLI reads all three to tell this API's own refusal from a
# page a proxy wrote, and it reads answers through that module alone. A
# test holds the titles' key set equal to the descriptions' above.
#
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

# What a prompt read answers for an agent this server is not serving.
# The name is not quoted back: it arrived in the path, and what is worth
# saying about it is where to look instead.
_UNLOADED_AGENT = (
    "this server is not serving an agent of that name. The agents a server can serve "
    "are the agents of the world it has installed, so one written since is served by "
    "the reload that installs it (`vinga-server config reload`), and one that never "
    "existed is a name nothing answers to. `vinga-server config list` shows the agents "
    "that are stored."
)

_UNLOADED_AGENT_DESCRIPTION = _description("unloaded-agent")

# Declared on the routes whose only 422 is the framework's own, so the
# document carries the sanitized shape this API actually answers with
# rather than FastAPI's default one, which lists the input it rejected
# per error. Nothing here validates a body, so this is the request that
# could not be read at all.
_MALFORMED_REQUEST_DESCRIPTION = _description("malformed-request")

# The shared 503 sentence is about actions, and says the reads in this
# namespace answer emptily. That is true of the MCP status read and
# false of this one: there is no honest empty prompt.
_NO_RUNTIME_PROMPT_DESCRIPTION = _description("no-runtime-prompt")

# The reload takes no body and addresses nothing, so the shared sentence
# for 422 (a stage that is not a stage, a MAC that is not one) cannot be
# what one of its own means. What it means instead is the whole of the
# guarantee the endpoint makes about a refusal.
_RELOAD_REFUSED_DESCRIPTION = _description("reload-refused")

# And its 409, which is neither of the two the shared sentence covers on
# this route: one apply at a time is this endpoint's own exclusion, and
# the snapshot-mode refusal is the one 409 in this API that retrying
# will not clear.
_RELOAD_HELD_DESCRIPTION = _description("reload-held")

# The diff read's three refusals that cannot inherit a shared sentence.
# It addresses nothing and carries no body, so the shared 422 (a stage
# that is not a stage, a MAC that is not one) cannot be what one of its
# own means; its 409 is not one of the three things the shared sentence
# lists; and the shared 503 says the reads in this namespace answer
# emptily, which is exactly what this one must not do.
_DIFF_REFUSED_DESCRIPTION = _description("diff-refused")

_DIFF_MOVED_DESCRIPTION = _description("diff-moved")

_NO_RUNTIME_DIFF_DESCRIPTION = _description("no-runtime-diff")

# And the identity read's, for the same reason as those two: the shared
# 503 sentence says the reads in this namespace answer emptily, and
# there is no empty identity. A version and a revision this application
# invented would be a claim about a server that is not there.
_NO_RUNTIME_INFO_DESCRIPTION = _description("no-runtime-info")

# What the caller is told, which is not the shared sentence, for the
# reason the diff's is not. Nothing of the deployment is named: what is
# missing is the server, not something the caller asked for wrongly.
_NO_RUNTIME_INFO = (
    "this API has no running server around it, so there is no deployment for it to "
    "describe. The version, the revision and the onboarding URL are facts of a running "
    "server and of the file half it booted from, and an application built without one "
    "has neither. A deployment reaches this read on its server's own port."
)

# The one response header this API sets on a route of its own, and the
# route it is set on is the one that answers a credential. `no-store`
# rather than `no-cache`: a proxy or a browser that revalidated would
# still have written the URL to a disk in the meantime.
NO_STORE = "no-store"
# The event stream's own two. Its 422 is never about addressing: it
# addresses nothing and carries no body, and the only things it parses
# are the three filters. Its 503 is the prompt read's case rather than
# the status read's: an empty stream from a server that is not there
# would look exactly like a quiet server.
_EVENTS_FILTER_DESCRIPTION = _description("events-filter-refused")

_NO_RUNTIME_EVENTS_DESCRIPTION = _description("no-runtime-events")

# What the stream says when there is no server around this application
# to have events. Fixed, and not the shared sentence, for the reason the
# diff's is not: the shared one says the reads in this namespace answer
# emptily, and an empty stream is the one answer this read must not
# give, since a server that is not there and a server that is saying
# nothing look identical on a stream.
_NO_RUNTIME_EVENTS = (
    "this API has no running server around it, so there are no events to stream. An "
    "empty stream is not the honest answer here, since it is exactly what a quiet "
    "server looks like. A deployment reaches this read on its server's own port."
)

# The three filters, and what each of them is. Fixed sentences carrying
# the rule and never the value: a filter arrives in a query string, and
# a refusal that quoted it back would put whatever was sent into a
# response and into whatever keeps one.
_EVENTS_DEVICE_REFUSED = (
    "device has to be a MAC address: six colon-separated or dash-separated hex pairs, "
    "for example aa:bb:cc:dd:ee:ff. What was sent is not quoted back"
)

_EVENTS_SESSION_REFUSED = (
    "session has to be a session's uuid hex, which is thirty-two hexadecimal "
    "characters, as the session listing answers with and as every event of that "
    "session carries. What was sent is not quoted back"
)

_EVENTS_LEVEL_REFUSED = (
    f"level has to be one of {', '.join(LEVELS)}, in any case, and it is a threshold "
    f"rather than a selection: absent means {logging.getLevelName(DEFAULT_LEVEL)} and "
    f"above, which is what the retained log carries. What was sent is not quoted back"
)

# The shape of a session id, which is what a filter is held to. The
# minted form (`uuid4().hex`) rather than the event vocabulary's wider
# machine form: what a reader can be given to filter on is what a
# listing prints and what a device session mints, and a filter admitting
# more than that would be admitting spellings nothing ever emits.
_SESSION_HEX = re.compile(r"\A[0-9a-fA-F]{32}\Z")

# How often an idle stream says it is still alive, in seconds.
#
# A comment line rather than an event, because a keepalive is not
# something that happened. The interval is a field of the runtime below
# rather than a constant read here, so a test does not wait it out and a
# deployment behind a proxy with a shorter idle timeout has somewhere to
# put a smaller number.
KEEPALIVE_S = 15.0

# What that line is. An SSE comment: a line beginning with a colon,
# which every reader ignores and every proxy counts as traffic.
_KEEPALIVE_LINE = b": keepalive\n\n"

# And what a reader that fell behind is told, as its own SSE event
# rather than as a field on the next one: a UI and the CLI render a loss
# the same way, and a stream that went quiet never means one.
_DROPPED_EVENT = "dropped"

# And what the caller is told, which is not the shared sentence: that
# one says the reads in this namespace answer emptily, and this read
# refuses precisely because an empty answer would be a claim about a
# server that is not there. A document that describes one thing and a
# body that says another leave the reader to decide which to believe.
_NO_RUNTIME_DIFF = (
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
_NO_STORED_WORLD = (
    "this server serves a configuration it was given rather than one it read from a "
    "store, so no stored configuration describes what it is running. The database it is "
    "pointed at holds whatever has been written to it since, which is not this server's "
    "world: comparing the two would report everything as pending, and applying it would "
    "replace what is running with a description of some other server. Nothing was "
    "changed, and making the request again will not help; a server started from a store "
    "answers both of these."
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
def open_store(database: DatabaseConfig) -> Iterator[StoreHandle]:
    """Open the configuration database `database` names and hold it.

    `open_database` rather than a bare engine, and deliberately: this is
    the only place the schema is brought up to date on the API's path,
    and an application built over a database nothing has migrated (a
    fresh deployment whose first act is an API write, which is what the
    integration lane's API-first path is) has to come up with a schema.
    `upgrade_to_head` is idempotent and cheap when the schema is
    current, so a server that migrated at boot pays one no-op check.

    A lock another writer is holding refuses here, as
    `DatabaseBusyError`, which is a `ConfigError` and therefore part of
    the boot failure taxonomy: a contended database at startup is a boot
    that refused with a sentence, not a traceback.
    """
    engine = open_database(database)
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


def engine_lifespan(runtime: "ApiRuntime", database: DatabaseConfig) -> Lifespan[FastAPI]:
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
        with open_store(database) as handle, installed(runtime, handle):
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
    whichever lifespan owns it and None until then; `conversations` and
    `erasures` are still per-request opens, which is a property that
    store documents for itself, and the second of them is how a deletion
    reaches a store no `ConversationStore` is holding open, which is
    every deployment with recording off. The other six are the live
    objects the server shares with this application, or the honest
    empties an application built without a server around it gets.

    `identity` is the odd one and says so: a value rather than a live
    object or a callable over one, because what it holds moves for
    neither a reload nor a request. The build is the process's and the
    onboarding URL is derived from the server section, which is the one
    part of a deployment's configuration that is genuinely read once. So
    the composition root resolves it in the same breath as everything
    else here and hands over the answer, and None is what an application
    with no server around it carries, since it can describe no
    deployment at all.
    """

    store: StoreHandle | None
    conversations: Callable[[], Iterator[Connection]]
    erasures: Callable[[], contextlib.AbstractContextManager[Connection]]
    memory: Callable[[], Iterator[Connection]]
    memory_writes: Callable[[], contextlib.AbstractContextManager[Connection]]
    loaded_agents: ServableAgents
    pending: PendingDevices
    mcp_servers: McpStatusSource | None
    reload: ConfigReloader | None
    agent_prompt: Callable[[str], Awaitable[Any]] | None
    config_diff: ConfigDiffReader | None
    snapshot_only: bool = False
    identity: RuntimeInfo | None = None
    # Everyone watching the running server's events, which the stream
    # route subscribes on behalf of one reader at a time. None is the
    # honest answer for an application built without a server around it,
    # and that route refuses rather than opening a stream that would say
    # nothing (#342).
    live: LiveEvents | None = None
    # How often an idle stream says it is still alive. A field rather
    # than the constant read at the route, so a test injects a short one
    # instead of waiting out the default.
    keepalive_s: float = KEEPALIVE_S


def build_api(
    token: str,
    database: DatabaseConfig,
    loaded_agents: ServableAgents | None = None,
    pending: PendingDevices | None = None,
    mcp_servers: McpStatusSource | None = None,
    reload: ConfigReloader | None = None,
    agent_prompt: Callable[[str], Awaitable[Any]] | None = None,
    config_diff: ConfigDiffReader | None = None,
    snapshot_only: bool = False,
    identity: RuntimeInfo | None = None,
    live: LiveEvents | None = None,
    keepalive_s: float = KEEPALIVE_S,
) -> FastAPI:
    """The sub-application the server mounts: the routes, gated.

    `token` is compared against every request's bearer token, and is
    resolved once at app build by `api_token` below rather than read per
    request, so a deployment that forgot the variable is refused at boot
    instead of at the first call.

    `loaded_agents` answers which agents the server around this
    application can be asked for, which is what a device write's
    acknowledgement needs in order to say whether the write is live: a
    binding to an agent this server is not serving waits for the reload
    that installs it. Asked per request rather than captured, because an
    apply moves the answer while the process runs (#191). None is the
    honest answer for an application built without a server around it,
    which can serve no agent at all and answers every write with the
    sentence that says so.

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

    `identity` says which deployment this is: the build that is running
    and the URL a board is onboarded at. A composed value rather than a
    callable, for the reason `ApiRuntime` gives, and it arrives from the
    composition root for the reason the three callables do: it is
    derived from the device-auth secret and the origin this deployment
    names itself by, and this application must not learn where either of
    those comes from. None is the honest answer without a server, and
    the route answers 503, like the prompt read and the diff: identity
    has no honest empty.
    """
    runtime = build_api_runtime(
        database,
        loaded_agents,
        pending,
        mcp_servers,
        reload,
        agent_prompt,
        config_diff,
        snapshot_only,
        identity,
        live,
        keepalive_s,
    )
    # A lifespan of its own, which runs only when this application is the
    # top-level one: it opens the configuration database and installs the
    # handle on the runtime above. Mounted on a server, Starlette runs no
    # lifespan here and the parent's does the installing instead, so the
    # engine has exactly one owner either way (#142).
    api = _application(engine_lifespan(runtime, database))
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
    database: DatabaseConfig,
    loaded_agents: ServableAgents | None = None,
    pending: PendingDevices | None = None,
    mcp_servers: McpStatusSource | None = None,
    reload: ConfigReloader | None = None,
    agent_prompt: Callable[[str], Awaitable[Any]] | None = None,
    config_diff: ConfigDiffReader | None = None,
    snapshot_only: bool = False,
    identity: RuntimeInfo | None = None,
    live: LiveEvents | None = None,
    keepalive_s: float = KEEPALIVE_S,
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
        # The same connection, read for the other schema in it. The
        # conversation reads need no more runtime fact than this: the
        # schema is there whether or not recording is on, because boot
        # migrates it either way, and a deployment that has switched
        # recording off still serves what it recorded.
        conversations=conversations.reader(database),
        # And the write half, opened for the length of one deletion. The
        # same argument as the read: nothing is held between requests,
        # and a deletion has to work in a deployment that records
        # nothing and therefore holds no writer.
        erasures=lambda: conversations.erasing(database),
        # And the third schema in the same database, opened the same way
        # and for the same two reasons: nothing is held between requests,
        # and this door has to work whether or not a reply path is
        # running, since what it exists for is auditing and correcting
        # what has accrued.
        memory=memory.reader(database),
        memory_writes=lambda: memory.writing(database),
        loaded_agents=_nothing_servable if loaded_agents is None else loaded_agents,
        pending=pending if pending is not None else _empty_pending(),
        mcp_servers=mcp_servers,
        reload=reload,
        agent_prompt=agent_prompt,
        config_diff=config_diff,
        snapshot_only=snapshot_only,
        identity=identity,
        live=live,
        keepalive_s=keepalive_s,
    )


def _empty_pending() -> PendingDevices:
    """A table for an application built without a server around it."""
    return PendingDevices()


def _nothing_servable() -> frozenset[str]:
    """What an application with no server around it can be asked for,
    which is no agent at all."""
    return frozenset()


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


_NO_ENGINE = (
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
        raise RuntimeError(_NO_ENGINE)
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
    """Which agents the server around this application can be asked for,
    right now.

    Taken from the application for the reason the store is: the document
    is rendered from an application built without a server, and nothing
    a route declares may depend on there being one. Asked per request
    rather than held, because an apply installs the stored agent set
    while the process runs, and a write acknowledged against a set this
    application captured at build would name a restart for an agent the
    server is already serving.
    """
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.loaded_agents()


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


def _identity(request: Request) -> RuntimeInfo | None:
    """Which deployment this is, as the composition root resolved it, or
    None for an application built without a server around it. Taken from
    the application for the reason the store is."""
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.identity


IdentityDep = Annotated[RuntimeInfo | None, Depends(_identity)]
def _live(request: Request) -> LiveEvents | None:
    """Everyone watching the running server's events, or None for an
    application built without one. Taken from the application for the
    reason the store is."""
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.live


LiveDep = Annotated[LiveEvents | None, Depends(_live)]


def _keepalive(request: Request) -> float:
    """How often an idle stream says it is still alive. A dependency for
    the reason the hub is one: it is a fact about the server around this
    application rather than a constant of the route."""
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.keepalive_s


KeepaliveDep = Annotated[float, Depends(_keepalive)]


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
    added later has to fight a name either. `info` is the newest of
    them and would have had the same fight: an operator may legally name
    an MCP server `info`.
    """

    @api.get(
        "/runtime/info",
        response_model=RuntimeInfo,
        responses=_problems(401, 503, instead={503: _NO_RUNTIME_INFO_DESCRIPTION}),
    )
    async def read_runtime_info(identity: IdentityDep, response: Response) -> RuntimeInfo:
        """Which deployment this is: the build that is running, and the
        URL a board is onboarded at.

        The read that answers "what server am I talking to", which
        nothing else here does: the entity reads say what is stored, the
        reads below say what is running, and none of them says which
        deployment is answering at all.

        It carries a credential, and it is the only read in this API
        that does. The onboarding URL's last segment is a key derived
        from the device-auth secret, standing in front of the token
        issuer, which is why the startup banner prints the origin and
        not the URL: a banner is a retained record shipped to whatever
        collects logs. Serving it here widens nothing, because the
        bearer token this request carried already grants every secret
        write this API has; what the design adds is that the answer is
        not retained anywhere on the way, hence `Cache-Control:
        no-store` below, and that the one client rendering it prints it
        on stdout alone.

        With onboarding off the URL and its provenance are null and the
        flag says why, because there is no short URL to serve and the
        path `server.ota_path` names is this deployment's secret rather
        than a substitute for one.

        An application built without a server around it answers 503,
        like the prompt read and the diff rather than like the MCP
        status read: an invented version is not an empty listing.
        """
        # The docstring is this endpoint's description in the committed
        # document, so what belongs to the handler is said here. Nothing
        # is composed: the composition root resolved the whole answer at
        # startup, because every fact in it is a fact of the process or
        # of the server section, and neither moves while this runs.
        if identity is None:
            raise NoRuntimeError(_NO_RUNTIME_INFO)
        response.headers["cache-control"] = NO_STORE
        return identity

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
                404: _UNLOADED_AGENT_DESCRIPTION,
                422: _MALFORMED_REQUEST_DESCRIPTION,
                503: _NO_RUNTIME_PROMPT_DESCRIPTION,
            },
        ),
    )
    async def read_agent_prompt(name: str, assemble: AgentPromptDep) -> dict[str, Any]:
        """The system prompt a session opening now as this agent would
        be sent, block by block.

        A runtime read rather than a stored one: it is assembled from
        the agents this server loaded, the MCP slice its registry is
        running and the memory store it writes, so it cannot disagree
        with what a session would be given, which is the whole point of
        it.

        It is a preview of a new session and says so, because there is
        no honest per-session answer to offer instead: a conversation
        already in progress holds the know-how half it assembled at its
        own activation, which may predate a reload, and what an operator
        audits is what the configuration produces now.

        It is keyed by the agent and by nothing else, so the memory it
        shows is the agent's own, and as much of it as a reply is sent:
        the newest facts, not everything the agent has been told, since
        the rest is reachable only by looking it up. The other two blocks
        a live session can be sent, what a conversation is currently
        keeping and what is known about the device it is happening on,
        belong to a session rather than to an agent and are absent here:
        a preview that invented a device to show its notes would be a
        second prompt assembler pretending to be the first. An agent
        whose `memory` section is off has no such block at all, here or
        in a reply, and is previewed without one.

        `async def`, like the status read beside it: the MCP slice is
        read on the loop that mutates it, so the answer cannot be half
        of one world and half of another. The memory read that follows
        is a database round trip and happens in a worker thread, which
        is what keeps an inspection request off the loop every live
        conversation is on.

        An agent this server is not serving is a 404 naming the reload,
        since that is what installs one; an application with no server
        around it answers 503, like the reload.
        """
        if assemble is None:
            raise NoRuntimeError(PROBLEM_DESCRIPTIONS[503])
        assembled = await assemble(name)
        if assembled is None:
            raise UnknownEntityError(_UNLOADED_AGENT)
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
                409: _RELOAD_HELD_DESCRIPTION,
                422: _RELOAD_REFUSED_DESCRIPTION,
            },
        ),
    )
    async def reload_config(
        servers: McpServersDep, reload: ReloadDep, snapshot_only: SnapshotOnlyDep
    ) -> ConfigReloadResult:
        """Apply the stored configuration to this running server.

        The one action in this namespace, and the one way a stored
        change reaches a running server. What it applies is the whole
        domain half: the provider entries and the MCP entries with the
        secrets stored on them, the
        agents' effective `mcp` grant lists, the shared prompt
        fragments, the agents themselves and the `agent_defaults` layer
        under them, which carries the stage every agent that names none
        of its own inherits. An agent this installs is one a device can
        be bound to and reach at its next check-in; one it removes is
        one no session can be opened as from the moment this answers,
        while a conversation already talking as it finishes on the world
        it was built from.
        What is not here is the server section, which is this process's
        own file and is never re-read: the port, the directories, the
        limits. Nothing this API writes is in it.

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

        The engines are the third half and the one that costs
        something. An entry whose definition and stored credential have
        not moved is carried into the new world as the object it already
        was, and is reported under `providers.reused`; one that was
        rewritten is built while the old one is still serving, reported
        under `providers.built`, and spoken through by the conversations
        that open after this answered. An engine a conversation is still
        speaking through is released when that conversation ends, so an
        apply that changes a local model briefly holds two of it.

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
            raise SnapshotOnlyError(_NO_STORED_WORLD)
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
                409: _DIFF_MOVED_DESCRIPTION,
                422: _DIFF_REFUSED_DESCRIPTION,
                503: _NO_RUNTIME_DIFF_DESCRIPTION,
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
        between the two. Everything in an answer here is waiting for the
        reload below, which is the whole of what applies this half;
        device bindings and the default agent are read as a device asks
        and are therefore never pending, so they carry their label and
        no lists.

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
            raise NoRuntimeError(_NO_RUNTIME_DIFF)
        if snapshot_only:
            raise SnapshotOnlyError(_NO_STORED_WORLD)
        return await diff()

    @api.get(
        "/runtime/events",
        response_class=_EventStream,
        # Stated rather than inferred. With no status code on the route,
        # the document generator reads the default off the response
        # class's `__init__` signature, and a streaming response takes
        # its content and nothing else; 200 is what this answers with
        # and saying so is cheaper than a parameter no caller passes.
        status_code=200,
        responses=_problems(
            401,
            422,
            503,
            instead={
                422: _EVENTS_FILTER_DESCRIPTION,
                503: _NO_RUNTIME_EVENTS_DESCRIPTION,
            },
        ),
    )
    async def read_runtime_events(
        live: LiveDep,
        keepalive_s: KeepaliveDep,
        device: str | None = None,
        session: str | None = None,
        level: str | None = None,
    ) -> "_EventStream":
        """What this server is saying right now, as it says it.

        The structured events, streamed as Server-Sent Events: one
        `data:` line per event, carrying the catalogued fields of that
        event plus two the stream owns, `ts`, the wall-clock instant it
        was emitted at, and `level`, its level's name. Those fields are
        the ones the retained JSON log carries too, under the same
        names; what the log has beside them is its own record-keeping,
        the channel it was written on and the rendered sentence, which
        this does not repeat. Nothing else crosses this surface, because
        there is nothing else: the events are metadata by construction,
        and what was said in a conversation is in the conversation store
        instead.

        Live and nothing but. There is no buffer behind this and no
        `since`: a stream carries what happens while it is open, and a
        reader that reconnects rejoins the present. What happened before
        is the conversation record's to answer, where a deployment
        enabled one.

        Three filters, applied as the events arrive. `device` takes a
        MAC in either separator and any case; `session` takes a
        session's uuid hex; `level` is a threshold, one of the four
        level names in any case, and defaults to INFO, which is what the
        retained log carries. An event that names no device or no
        session passes such a filter only when none is set, so a
        device-filtered tail is that board's traffic rather than the
        whole server's.

        A reader that falls behind loses its oldest events rather than
        slowing the server down, and is told: a `dropped` event carries
        how many went since the last delivery. An idle stream sends a
        comment line every few seconds so a proxy does not close it, and
        the stream ends when the client goes away or when the server
        shuts down.
        """
        # This docstring is the endpoint's description in the committed
        # document, so what belongs to the handler rather than to the
        # contract is said here. The filters are read before anything is
        # opened, so a filter that cannot be read is a refusal with a
        # status rather than a stream that opens and then dies; the
        # subscription is the body's own, taken beside the `finally`
        # that gives it back, for the reason `_sse_body` states.
        if live is None:
            raise NoRuntimeError(_NO_RUNTIME_EVENTS)
        return _EventStream(_sse_body(live, _filters(device, session, level), keepalive_s))


class _EventStream(StreamingResponse):
    """The event stream's response: the SSE media type, and the one
    header that keeps it from being kept.

    A subclass rather than arguments at the call site, because the
    document is rendered from the route's `response_class` and a bare
    `StreamingResponse` declares no media type at all, which would
    publish this route as answering JSON. `no-store` because a live
    stream is the least cacheable thing this API has and an
    intermediary that buffered it would turn a tail into a replay.
    """

    # Read from `responses.py` rather than written here, because the
    # client half checks it: `config/cli.py` will not read a body that
    # did not arrive under this type, and two spellings of one wire fact
    # would be a tail that silently stopped reading a stream this route
    # still serves.
    media_type = EVENT_STREAM_MEDIA_TYPE

    def __init__(self, content: AsyncIterator[bytes]) -> None:
        super().__init__(content, headers={"Cache-Control": "no-store"})


async def _sse_body(
    live: LiveEvents, filters: Filters, keepalive_s: float
) -> AsyncIterator[bytes]:
    """One reader's stream, until it goes away or the server does.

    The keepalive is a timeout on the wait rather than a task beside it:
    nothing arriving for `keepalive_s` is exactly when a comment line is
    due, and a stream with events in it needs no timer at all.

    The `finally` is the whole of the cleanup contract. It runs when the
    client disconnects (the generator is closed), when the hub closes
    (the subscription ends and this returns), and when anything here
    raises, so a subscription is never left on a hub with nobody
    reading it.

    Which is why the subscription is taken HERE, one statement above
    that `finally`, rather than by the handler that built this. A body
    is not iterated until the response has begun, Starlette runs it
    beside a disconnect listener and cancels the one when the other
    returns, and a generator that never started runs no `finally` when
    it is closed. A client that went away while `http.response.start`
    was in flight would therefore leave a queue attached to the hub for
    the life of the process, filled by every emission and read by
    nobody. Subscribing inside the body makes "subscribed" and "will be
    given back" the same moment; a request that never gets this far
    never subscribed at all. The filters are read by the handler and
    passed in, because a filter that cannot be read is a refusal and
    must be answered before a stream is opened.
    """
    subscription = live.subscribe(filters)
    try:
        while True:
            item = await subscription.next(keepalive_s)
            if item is None:
                if subscription.ended:
                    return
                yield _KEEPALIVE_LINE
                continue
            yield _sse_line(item)
    finally:
        live.unsubscribe(subscription)


def _sse_line(item: Streamed | Dropped) -> bytes:
    """One item as the wire carries it.

    A dropped count is its own named event rather than a field on the
    next one, so a reader renders a loss as a loss; everything else is
    an unnamed `data:` line, which is what `EventSource` delivers as its
    default message. The JSON is compact and cannot carry a newline, so
    one item is one `data:` line whatever an event's values hold.
    """
    if isinstance(item, Dropped):
        body = json.dumps({_DROPPED_EVENT: item.count}, separators=(",", ":"))
        return f"event: {_DROPPED_EVENT}\ndata: {body}\n\n".encode()
    return f"data: {json.dumps(item.fields, separators=(',', ':'))}\n\n".encode()


def _filters(device: str | None, session: str | None, level: str | None) -> Filters:
    """The three query arguments as the hub's own filter.

    Read here rather than in the hub for the reason every other query
    argument in this API is read here: what a filter is spelled as, and
    what a caller is told when it is not, is transport. What the values
    mean is the hub's, which is why what comes back is its type and not
    three strings.

    Every refusal is a fixed sentence carrying the rule, and none of
    them quotes what was sent: a query string reaches a response body,
    a proxy log and a browser's history.
    """
    return Filters(
        device=_device_filter(device),
        session=_session_filter(session),
        level=_level_filter(level),
    )


def _device_filter(value: str | None) -> str | None:
    """The device filter, normalized the way every other MAC in this
    project is, so `AA-BB-...` and `aa:bb:...` tail the same board."""
    if value is None:
        return None
    problem: ConfigError | None = None
    try:
        return normalize_mac(value)
    except ValueError:
        # Built here and raised outside the arm, this codebase's rule: a
        # refusal raised inside one carries what it caught as its
        # `__context__`, and the caller is a response body.
        problem = ConfigError(_EVENTS_DEVICE_REFUSED)
    raise problem


def _session_filter(value: str | None) -> str | None:
    """The session filter, lower-cased so a listing's id and an id typed
    in capitals reach the same session."""
    if value is None:
        return None
    if _SESSION_HEX.match(value) is None:
        raise ConfigError(_EVENTS_SESSION_REFUSED)
    return value.lower()


def _level_filter(value: str | None) -> int:
    """The level threshold, by name and in any case."""
    if value is None:
        return DEFAULT_LEVEL
    level = LEVELS.get(value.strip().upper())
    if level is None:
        raise ConfigError(_EVENTS_LEVEL_REFUSED)
    return level


# How a client finds the options a provider write may carry
#
# The body is taken unread (`RawBody`), for the reason the module
# docstring gives: FastAPI's own validation echoes what it rejected, and
# a fragment can carry a pasted credential. So the route cannot declare a
# discriminated request schema keyed on `type`, and a client reading the
# document would find `ProviderConfig` with its `extra` open and nothing
# more.
#
# The mapping is what closes that gap without reopening the refusal
# question: the description names, per declared stage and type, the
# component that states what that type accepts, and those components are
# injected beside the entity models. Derived from the declaration, so a
# type converting in a later PR appears here by existing (#88).
_PROVIDER_WRITE = (
    "Create or replace one provider from a fragment in the shape the YAML section "
    "had. The running server builds it again at the next reload, and the "
    "conversations that open after that speak through the new one."
)

_PROVIDER_OPTIONS_MAPPING = (
    "The options a fragment may carry beyond the fields of `ProviderConfig` are "
    "whatever its `type` takes. This body is validated by the server rather than "
    "described as a discriminated schema here, so the types that declare their "
    "options state them as components of their own, named for the stage and the "
    "type: {mapping}. Every other type passes its options through undeclared, and "
    "the example fragments shipped with the server document those."
)


def _provider_write_description() -> str:
    """The provider PUT's description, with the component mapping under
    it. One sentence per declared pair, in the declaration's order."""
    mapping = ", ".join(
        f"an entry in the `{stage}` stage with `type: {type_name}` carries "
        f"`{component_name(stage, type_name)}`"
        for stage, type_name, _ in declared_options()
    )
    return f"{_PROVIDER_WRITE}\n\n{_PROVIDER_OPTIONS_MAPPING.format(mapping=mapping)}"


PROVIDER_WRITE_DESCRIPTION = _provider_write_description()


def _entity_writes(api: FastAPI) -> None:
    """Every commanded kind's writes, deletes and credential slots.

    Written out for the reason the reads above are, and in the order the
    document has: per kind, the entity write, its delete, and then the
    two credential routes for the two kinds that can hold one.

    A handler makes one repository call and answers with what it did and
    when it applies. The sentence is written here, in the handler, and
    the timing is the kind's own `notice`, which is a descriptor fact
    because it is about what was written rather than about the route
    that wrote it. There is one spelling of each of them, because there
    is one write path: the CLI is a client of these routes and prints
    what they answered.

    A fragment is handed to the repository unread (`RawBody`), which is
    the rule the module docstring gives: FastAPI's own validation echoes
    what it rejected, and a fragment can carry a pasted credential.
    """

    @api.put(
        "/providers/{stage}/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=request_body(_PROVIDER.model),
        description=PROVIDER_WRITE_DESCRIPTION,
    )
    def write_provider(
        stage: str, name: str, body: RawBody, store: StoreDep
    ) -> dict[str, Any]:
        # The description is passed rather than left as this docstring,
        # which is the one route in this file where the two differ. What
        # a client needs here is not only what the write does but which
        # component states the options for the stage and type it is
        # writing, and that list is derived from the declaration rather
        # than typed into a docstring that would go stale as the types
        # convert (#88).
        """Create or replace one provider from a fragment in the shape
        the YAML section had. The running server builds it again at the
        next reload, and the conversations that open after that speak
        through the new one."""
        store.set_provider(stage, name, body)
        return _acknowledge(f"provider {stage}.{name}", _PROVIDER.notice)

    @api.delete(
        "/providers/{stage}/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_provider(stage: str, name: str, store: StoreDep) -> dict[str, Any]:
        """Delete one provider, and the secrets stored on it. Refused
        while an agent or the agent defaults still name it."""
        store.delete_provider(stage, name)
        return _acknowledge(
            f"provider {stage}.{name} deleted, with its stored secrets", _PROVIDER.notice
        )

    @api.put(
        "/providers/{stage}/{name}/secrets/{slot}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
        openapi_extra=request_body(SecretValue),
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
        return _acknowledge(f"secret for {location.describe()}", _PROVIDER.notice)

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
        return _acknowledge(f"secret for {location.describe()} cleared", _PROVIDER.notice)

    @api.put(
        "/mcp-servers/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=request_body(_MCP_SERVER.model),
    )
    def write_mcp_server(name: str, body: RawBody, store: StoreDep) -> dict[str, Any]:
        """Create or replace one MCP server. The running server applies
        it at the next reload, with no restart."""
        store.set_mcp_server(name, body)
        return _acknowledge(f"mcp-server {name}", _MCP_SERVER.notice)

    @api.delete(
        "/mcp-servers/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_mcp_server(name: str, store: StoreDep) -> dict[str, Any]:
        """Delete one MCP server, and the secrets stored on it. The
        running server stops it at the next reload."""
        store.delete_mcp_server(name)
        return _acknowledge(
            f"mcp-server {name} deleted, with its stored secrets", _MCP_SERVER.notice
        )

    @api.put(
        "/mcp-servers/{name}/secrets/{slot}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
        openapi_extra=request_body(SecretValue),
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
        return _acknowledge(f"secret for {location.describe()}", _MCP_SERVER.notice)

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
        return _acknowledge(f"secret for {location.describe()} cleared", _MCP_SERVER.notice)

    @api.put(
        "/prompt-fragments/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=request_body(_PROMPT_FRAGMENT.model),
    )
    def write_prompt_fragment(
        name: str, body: RawBody, store: StoreDep
    ) -> dict[str, Any]:
        """Create or replace one shared prompt fragment.

        It carries the reload sentence: a fragment is prompt text, the
        reload re-reads the whole `prompt_fragments` kind, and prompt
        text is assembled at an activation, so the new words reach every
        agent that includes this fragment at that agent's next
        activation. Which agents those are is whichever layer names it,
        and both layers are the same reload's: an agent's own
        `prompt_includes`, and the `agent_defaults.prompt_includes` that
        every agent naming no list of its own inherits."""
        store.set_prompt_fragment(name, body)
        return _acknowledge(f"prompt-fragment {name}", _PROMPT_FRAGMENT.notice)

    @api.delete(
        "/prompt-fragments/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_prompt_fragment(name: str, store: StoreDep) -> dict[str, Any]:
        """Delete one shared prompt fragment. Refused while any layer
        still includes it."""
        store.delete_prompt_fragment(name)
        return _acknowledge(f"prompt-fragment {name} deleted", _PROMPT_FRAGMENT.notice)

    @api.put(
        "/agents/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=request_body(_AGENT.model),
    )
    def write_agent(name: str, body: RawBody, store: StoreDep) -> dict[str, Any]:
        """Create or replace one agent. Every provider and MCP server it
        names has to exist already, which is what the natural creation
        order is about."""
        store.set_agent(name, body)
        return _acknowledge(f"agent {name}", _AGENT.notice)

    @api.delete(
        "/agents/{name}",
        response_model=Acknowledgement,
        responses=_problems(401, 404, 409, 422, 500),
    )
    def remove_agent(name: str, store: StoreDep) -> dict[str, Any]:
        """Delete one agent. Refused while a device binding or the
        default agent still names it."""
        store.delete_agent(name)
        return _acknowledge(f"agent {name} deleted", _AGENT.notice)

    @api.put(
        "/agent-defaults",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=request_body(_AGENT_DEFAULTS.model),
    )
    def write_agent_defaults(body: RawBody, store: StoreDep) -> dict[str, Any]:
        """Replace what every agent uses unless it names something else.
        One entry for the whole deployment, so this is a replace and
        there is nothing to delete."""
        store.set_agent_defaults(body)
        return _acknowledge("agent-defaults", _AGENT_DEFAULTS.notice)


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
        openapi_extra=request_body(DeviceBinding),
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
            raise ClaimInFlightError(_CODE_IN_FLIGHT)
        if claim.device is None:
            raise UnknownEntityError(_UNKNOWN_CODE)
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
            raise ConfigError(_CLAIM_REFUSED)
        pending.consume(code)
        # Both the line and the notice are built from what the row
        # holds, never from what the request sent, exactly as the write
        # by MAC below builds them: a name arriving with spaces around
        # it binds the agent it names, and an acknowledgement derived
        # from the request would have called that agent unloaded and
        # sent the operator to restart a server that is already serving
        # it.
        return _acknowledge(
            f"device {bound.mac} bound to {', '.join(bound.agents)}",
            _binding_notice(_unloaded(bound.agents, loaded), snapshot_only),
        )

    @api.put(
        "/devices/{mac}",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=request_body(DeviceBinding),
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
        # write made where this table cannot be reached, such as a
        # second process.
        pending.retire(bound.mac)
        # Both the line and the notice are built from what the row
        # holds, never from what the request sent: a name arriving with
        # spaces around it binds the agent it names, and an
        # acknowledgement derived from the request would have called
        # that agent unloaded and sent the operator to restart a server
        # that is already serving it.
        return _acknowledge(
            f"device {bound.mac} bound to {', '.join(bound.agents)}",
            _binding_notice(_unloaded(bound.agents, loaded), snapshot_only),
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
            f"device {store.delete_device(mac)} deleted",
            _binding_notice(snapshot_only=snapshot_only),
        )

    @api.put(
        "/default-agent",
        response_model=Acknowledgement,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=request_body(DefaultAgentName),
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
            f"default agent {name}",
            _binding_notice(_unloaded([name], loaded), snapshot_only),
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
            _CLEARED_DEFAULT_AGENT, _binding_notice(snapshot_only=snapshot_only)
        )

    @api.post(
        "/apply",
        response_model=AppliedDocument,
        responses=_problems(401, 409, 422, 500),
        openapi_extra=request_body(DomainConfig),
    )
    def apply_document(
        request: Request,
        body: RawBody,
        store: StoreDep,
        loaded: LoadedAgentsDep,
        pending: PendingDep,
        snapshot_only: SnapshotOnlyDep,
    ) -> dict[str, Any]:
        """Write a whole document: every entry it names, in one
        transaction, or none of them.

        The body is a partial domain configuration: its top-level keys
        are the sections of the configuration, an entity's body is
        exactly the fragment its own PUT takes, and the two settings are
        in the shape the configuration holds them in rather than the
        shape their own routes take, because this is the configuration
        and not a batch of requests.

        Applying is additive. A section or an entry the document does
        not name is left alone, an empty mapping adds nothing, and
        nothing here deletes: pruning a store down to a document is a
        different verb with different stakes, secret deletion among
        them, and it is deliberately not this one. The single entry that
        takes something away is `default_agent: null`, which is the
        explicit unset; leaving the key out says nothing about it.

        Idempotent by comparison: an entry that is already exactly what
        the document says is answered `unchanged`, with nothing written
        and nothing to apply, so the same document twice is a no-op.

        Refused whole. The references are checked once against the
        configuration the whole document would leave, which is what lets
        one document create an agent and bind a device to it in the same
        breath; any refusal names every mistake at once, in the
        sentences the single writes earn, and leaves the store exactly
        as it was.
        """
        _bounded(request)
        applied = store.apply(body)
        # Everything below runs after the transaction has committed, so
        # nothing here can be true of a document that was refused.
        #
        # A device this document bound is configured now, so it is not
        # one an operator may still claim by the code it was showing,
        # and a default agent covers every device that has no binding of
        # its own, which is every device in the pending table. Both are
        # the housekeeping the two settings routes do for the same acts,
        # and both are done for an unchanged row as well as a changed
        # one: what retires a code is the world the document describes,
        # not whether this request was the one that wrote it.
        for entry in applied:
            if entry.section == "devices":
                pending.retire(entry.identity)
        if any(entry.section == "default_agent" and entry.agents for entry in applied):
            pending.retire_all()
        return {
            "entries": [_applied(entry, loaded, snapshot_only) for entry in applied]
        }


# How large a document this API will read.
#
# Request hygiene beside the repository's own entry-count limit, and the
# weaker of the two: the framework has read the body by the time a
# handler runs, so what this bounds is what reaches the repository
# rather than what the socket accepted, and a request that declares no
# length is bounded by the entry count alone. The entry count is the one
# that bounds the transaction, which is the thing worth bounding.
APPLY_BODY_LIMIT = 1_000_000

_TOO_LARGE = (
    f"the document is larger than this endpoint reads, which is {APPLY_BODY_LIMIT} "
    f"bytes. Nothing was changed and nothing sent is quoted back; apply it in several "
    f"documents"
)


def _bounded(request: Request) -> None:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > APPLY_BODY_LIMIT:
        raise ConfigError(_TOO_LARGE)


# When a write of each section takes effect, read off the registry
# rather than written out: an applied document writes the same kinds the
# entity routes write, so it answers the same sentences. The two
# settings are absent, because theirs depend on what the running server
# is serving and are computed per entry below.
_SECTION_NOTICE: dict[str, str] = {
    descriptor.moved_key: descriptor.notice for descriptor in entities.ENTITIES
}


def _applied(
    entry: Applied, loaded: Collection[str], snapshot_only: bool
) -> dict[str, Any]:
    """One entry of an applied document, as the answer carries it.

    The repository answers with the canonical outcome and nothing else,
    because the rest is not a fact it holds: when a change takes effect
    depends on whether this server is serving the agents a binding
    names and on whether it reads a store at all, which is what the
    single writes ask the same two dependencies about.
    """
    return {
        "section": entry.section,
        "identity": entry.identity,
        "outcome": "wrote" if entry.wrote else "unchanged",
        "notice": _applied_notice(entry, loaded, snapshot_only) if entry.wrote else None,
    }


def _applied_notice(
    entry: Applied, loaded: Collection[str], snapshot_only: bool
) -> str:
    """When one applied entry takes effect: the settings' notice where
    it depends on the running server, and the kind's own otherwise."""
    if entry.section in _SECTION_NOTICE:
        return _SECTION_NOTICE[entry.section]
    return _binding_notice(_unloaded(entry.agents, loaded), snapshot_only)


def _acknowledge(what: str, notice: str = RESTART_NOTICE) -> dict[str, str]:
    """What a write answers with. The start sentence is the default
    because it promises nothing: no kind this API writes carries it any
    more, so a new write route that forgot to name its own boundary
    reads as conservative rather than as a promise the server cannot
    keep."""
    return {"wrote": what, "notice": notice}


def _binding_notice(unloaded: Sequence[str] = (), snapshot_only: bool = False) -> str:
    """When a device write takes effect, which depends on two things.

    The binding itself is live. The agent it names may not be: a server
    builds an agent's pipeline when it installs a world, so a binding to
    an agent written since the last one resolves to nothing until the
    reload that installs it, and saying "no restart is needed" there
    would be a promise the device cannot keep for a reason an operator
    could not guess. `unloaded` is the names this server is not serving,
    asked of the generation that is current at the moment of the write
    rather than of the world this process booted, and empty when every
    one of them is being served and the write is live on its own.

    `snapshot_only` is the server that reads no store at all, and it
    answers before either of those: what is live about a binding is that
    a running server re-reads the rows, and a server serving a
    configuration it was handed re-reads nothing. The one true thing
    left to say is that the write is stored, which is what the sentence
    says. Written here rather than at the five call sites because this
    is where a device write's answer is decided, and there is no second
    write path that decides it.
    """
    if snapshot_only:
        return SNAPSHOT_NOTICE
    return BINDING_UNSERVED_NOTICE if unloaded else BINDING_NOTICE


def _unloaded(agents: Sequence[str], loaded: Collection[str]) -> list[str]:
    """The names a write mentioned that this server has not built an
    agent for, which is what stands between the write and the device."""
    return [name for name in agents if name not in loaded]


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
    value = _sole_value(body, "agents", _DEVICE_BODY)
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise ConfigError(_DEVICE_BODY)
    return value


def _name(body: object) -> str:
    value = _sole_value(body, "name", _DEFAULT_AGENT_BODY)
    if not isinstance(value, str):
        raise ConfigError(_DEFAULT_AGENT_BODY)
    return value


def _secret(body: object) -> str:
    value = _sole_value(body, "secret", _SECRET_BODY)
    if not isinstance(value, str) or not value:
        raise ConfigError(_SECRET_BODY)
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
            401, _UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"}
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
            await problem_response(500, _UNEXPECTED)(scope, receive, send)


def _application(lifespan: Lifespan[FastAPI] | None = None) -> FastAPI:
    """The sub-application without its gate: what the server mounts and
    what the document is rendered from, so the two cannot disagree about
    the routes.

    `lifespan` is how the standalone path owns its engine, and is None
    for the document, which is rendered from an application nobody
    serves and which therefore opens nothing.
    """
    api = FastAPI(
        title=_API_TITLE,
        version=API_VERSION,
        description=_API_DESCRIPTION,
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
        # router's default answers `/config/` or `/sessions/` with a
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
    # And what this deployment remembers, registered the same way and for
    # the same reason: the memory schema is granted to no analyst role,
    # so this namespace is the surface an operator audits and corrects it
    # through, and a route the committed document does not carry is not
    # in the contract.
    memory.routes(api, _problems)
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
    return problem_response(422, _MALFORMED_REQUEST)


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

    The provider option models ride in the same way and under a name of
    their own, `<Stage><Type>Options`, because their class name is not
    the address: what selects one is the stage and the type together
    (#88). They are the structural half of what the provider PUT's
    description says in words; without them a client reading this
    document would be told that a type declares its options and given no
    way to read them.
    """
    schemas: dict[str, Any] = {}
    for model in ENTITY_MODELS + REQUEST_MODELS + DOCUMENT_MODELS + PROBLEM_MODELS:
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    for stage, type_name, model in declared_options():
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[component_name(stage, type_name)] = schema
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
