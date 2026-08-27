"""The conversation store's reads: three GETs on the gated /api.

The route functions live here and are registered by `config/api.py`'s
`_application()`, which is the application `document()` renders and the
one the server mounts, so a route cannot be served without being in the
committed contract. What travels the other way is only runtime fact: the
database connection, attached by `build_api` as the per-request reader
below. Registration takes `_problems` as an argument rather than
importing it, because the configuration API imports this module and the
import may not go both ways.

Nothing here decides what is stored. The writer applies the storage
switches (`store.py`), the schema says what a column means
(`schema.py`), and a content column comes back exactly as it was
written: null where text storage was off, with the session's own `text`
flag beside it saying which reading the null deserves.

Three transport rules the routes hold to:

- **Cursors are the monotonic row ids and nothing else.** No timestamps
  inside them, no opaque encoding to version. The list pages backwards
  (`id` strictly below the cursor, newest first) and the timeline pages
  forwards (`id` strictly after it), which is the reconcile direction.
- **A refused argument is never quoted back.** A limit, a cursor or a
  device that cannot be read answers with a fixed sentence describing
  what the argument has to be. What arrived is the caller's, and a
  refusal that echoed it would be the one place this API prints
  something it was handed.
- **A store with no rows answers its ordinary empty shapes.** There is
  no 404 for "this deployment never recorded" any more, and that is a
  deliberate contract change (#283): the distinction it drew was
  between a file that existed and one that did not, and there is no
  file. Boot migrates the schema whether or not recording is on, so
  what a deployment that never recorded has is empty tables, and an
  empty list is the honest answer to a question about them. The 404
  that remains is the one about a session id.

Every read opens a connection for the length of one request through
`db.read_engine`: no migration, no advisory lock, and a repeatable-read
snapshot that cannot write.
"""

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import Depends, FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ColumnElement, Connection, Table, func, select

from vinga_server.config.loader import ConfigError, UnknownEntityError
from vinga_server.config.models import DatabaseConfig, normalize_mac
from vinga_server.conversations.schema import (
    CLOSE_REASONS,
    TOOL_SOURCES,
    events,
    sessions,
    tool_invocations,
    turns,
)
from vinga_server.db import read_engine

if TYPE_CHECKING:
    # The name only, for the annotation in `_reader`: the configuration
    # API imports this module to mount these reads, so a module-scope
    # import in this direction would not load. Nothing here runs at
    # runtime.
    from vinga_server.config.api import ApiRuntime

# The two closed sets, in the document, built from the tuples the schema
# already declares rather than written out again here. A token added to
# either one reaches the contract by being added once; a document that
# spelled them out could disagree with the rows it describes, which is
# the whole failure mode a closed set exists to prevent.
ToolSource = Literal[*TOOL_SOURCES]
CloseReason = Literal[*CLOSE_REASONS]

# How many rows a page holds when the caller says nothing, and the most
# it may ask for. The maximum is the contract rather than a courtesy: a
# page is assembled in memory and a turn carries its tool invocations
# nested, so an unbounded limit would be an unbounded response.
LIMIT_DEFAULT = 50
LIMIT_MAX = 200

# The range of the `bigint` identity columns the cursors are. True by
# declaration since the schema says `bigint` (#283), rather than by
# folklore about what a row id happens to be. A cursor beyond it is
# refused here rather than bound into a statement, where it would be a
# driver error and a 500 instead of the caller's own mistake.
MAX_ROW_ID = 2**63 - 1

# What a refused argument is told. Each says what the argument has to
# be and none of them repeats what arrived: these are the only values
# this API is handed outside a request body, and the rule that a body is
# never quoted back is not a weaker rule out here.
_LIMIT_REFUSED = (
    f"limit has to be a whole number between 1 and {LIMIT_MAX}, or absent for "
    f"{LIMIT_DEFAULT}. What was sent is not quoted back"
)

_CURSOR_REFUSED = (
    "cursor has to be one of the row ids this API answers with, as a whole number, "
    "or absent for the first page. What was sent is not quoted back"
)

_DEVICE_REFUSED = (
    "device has to be a MAC address: six colon-separated or dash-separated hex "
    "pairs, for example aa:bb:cc:dd:ee:ff. What was sent is not quoted back"
)

# The one 404, which does not name what the request asked for: a session
# id arrives in the path, and what is worth saying about it is where to
# look instead.
#
# There used to be a second, for a deployment with no `conversations.db`
# at all. It retired with the file (#283): the schema is migrated at
# every boot, so "no store" is not a state that exists, and a deployment
# that never recorded answers the empty shapes rather than a refusal.
_UNKNOWN_SESSION = (
    "no session of that id is in the conversation store. The id is the session's uuid "
    "hex, which its events carry and its capture triplet is named after; a session "
    "older than server.conversations.retention_days has been pruned, and a pruned "
    "session is gone with its turns and its events."
)

# What each refusal means here, where the shared sentence would not be
# true. 404 is two cases and the status alone cannot tell them apart;
# 422 is never about addressing, since the only things these routes
# parse are a limit, a cursor and a device; and 500 is about the
# conversation store rather than the stored configuration.
PROBLEMS_INSTEAD: dict[int, str] = {
    404: "No session of that id is in the conversation store.",
    422: (
        "One of the query arguments could not be read: the limit, the cursor or the "
        "device filter. Nothing sent is quoted back."
    ),
    500: (
        "The conversation store cannot be read, or the request failed for a reason "
        "that is not the caller's. The details are in the server's log."
    ),
}

# What a listing answers per session. A summary rather than the row: the
# detail read is one request away, and a listing that carried every
# session's providers would be paying for a column nobody paginates for.
SUMMARY_COLUMNS: tuple[ColumnElement[Any], ...] = (
    sessions.c.id,
    sessions.c.session,
    sessions.c.device,
    sessions.c.agent,
    sessions.c.started_at,
    sessions.c.closed_at,
    sessions.c.duration_s,
    sessions.c.close_reason,
)

# A turn as the timeline answers it: its columns, minus the session it
# belongs to, which is the path it was asked for by.
TURN_COLUMNS: tuple[ColumnElement[Any], ...] = tuple(
    column for column in turns.c if column.name != "session"
)

# One tool invocation, minus its own row id and the two references that
# say where it belongs: it is nested inside the turn it belongs to, in
# the order the model issued it, so both are already answered.
INVOCATION_COLUMNS: tuple[ColumnElement[Any], ...] = tuple(
    column for column in tool_invocations.c if column.name not in {"id", "turn", "session"}
)


# The transport shapes
#
# Declared as response models so the committed document carries real
# schemas rather than the empty objects an untyped dictionary return
# would produce. They are shapes and not a second policy layer: what the
# writer stored is what these carry, nulls included.


class ToolInvocation(BaseModel):
    """One call a turn issued, as the timeline nests it.

    The transport shape of a `tool_invocations` row, not the record the
    pipeline hands the writer, which is the dataclass of the same name
    in `records.py`.
    """

    model_config = ConfigDict(extra="forbid")

    position: int = Field(
        description=(
            "Where this call sat in the round's call list, as the model issued it, "
            "counted from zero and with handovers included. The rows are nested in "
            "this order; it is the model's order and not the order they finished in."
        )
    )
    source: ToolSource = Field(
        description=(
            "Where the call was routed: `builtin` for one this application authors, "
            "`device` for one the device published, `mcp` for one an MCP entry owns, "
            "`unknown` for a name nothing answered to. Classified before the call ran. "
            "A closed set here because it is a closed set in the database, which holds "
            "the same four tokens under a check constraint."
        )
    )
    entry: str | None = Field(
        description=(
            "The owning MCP entry's configured name for an `mcp` call, and null "
            "otherwise. A name this deployment chose, so it survives text-off."
        )
    )
    name: str | None = Field(
        description=(
            "The called tool's name, and null when text storage was off for the "
            "session: a tool's name originates off this server, a device's "
            "self-description or an MCP far side, exactly as its result does."
        )
    )
    malformed: bool = Field(
        description="Whether the model's arguments were not a JSON object."
    )
    arguments: dict[str, Any] | None = Field(
        description=(
            "What the model passed, null under text-off and null when the call was "
            "malformed, which is what `malformed` above tells them apart by."
        )
    )
    result: str | None = Field(
        description="What the call answered, a refusal included. Null under text-off."
    )
    is_error: bool = Field(description="Whether the call answered as an error.")
    duration_ms: int | None = Field(
        description=(
            "How long the call took, in milliseconds. Null where nothing ran, as for a "
            "refused or a successful handover, and null under metrics-off."
        )
    )


class TurnLeg(BaseModel):
    """One agent's share of a turn a handover split.

    The transport shape of one entry of `turns.legs`, whose halves
    follow different storage switches: the text is content and the token
    counts are measurements, which is why a leg exists at all rather
    than the turn's totals being the whole story. The totals blend
    agents that may use different models; this is where they come apart
    again.
    """

    model_config = ConfigDict(extra="forbid")

    agent: str | None = Field(
        description=(
            "The agent whose leg this is, and null for a session that never activated "
            "one."
        )
    )
    text: str | None = Field(
        description=(
            "What this agent said, null under text-off, and null for an agent that "
            "took part without speaking: one that asked for the handover said nothing "
            "and spent tokens all the same."
        )
    )
    input_tokens: int | None = Field(
        description=(
            "Input tokens this agent spent on the turn. Null when the provider "
            "reported no usage, and under metrics-off."
        )
    )
    output_tokens: int | None = Field(
        description=(
            "Output tokens this agent spent on the turn. Null when the provider "
            "reported no usage, and under metrics-off."
        )
    )


class ConversationTurn(BaseModel):
    """One utterance and the reply it got, with the calls the reply made."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(
        description=(
            "The turn's monotonic row id, never reused. It is this timeline's cursor: "
            "a page asked for with it holds the turns after it."
        )
    )
    conversation: str = Field(
        description=(
            "The thread this turn belongs to, by its uuid hex. A turn names both "
            "its session and its conversation, which is what makes the session "
            "timeline and the thread two readings of one set of rows: the turns of "
            "one session can belong to several threads, and one thread's turns can "
            "come from several sessions."
        )
    )
    t_ms: int = Field(
        description=(
            "The utterance's offset from session open, in milliseconds, aligned with "
            "its `heard` event and with the capture's audio for the same session."
        )
    )
    agent: str | None = Field(
        description=(
            "The agent that owns this turn, which is the one it started with and "
            "therefore the one whose thread it is on. A handover makes it different "
            "from the session's and from the agent that finished the reply; the legs "
            "below are where a split reply comes apart."
        )
    )
    heard: str | None = Field(
        description="What was said to the device, as transcribed. Null under text-off."
    )
    heard_duration_s: float | None = Field(
        description="How long the utterance lasted, in seconds. Null under metrics-off."
    )
    language: str | None = Field(
        description=(
            "The language the transcript was recognized as. Neither a measured number "
            "nor conversation text, so it survives both switches."
        )
    )
    language_confidence: float | None = Field(
        description="How sure the recognizer was of that language. Null under metrics-off."
    )
    reply: str | None = Field(
        description=(
            "What the assistant said, the legs joined. Null under text-off, and null "
            "when the reply spoke nothing."
        )
    )
    legs: list[TurnLeg] | None = Field(
        description=(
            "One entry per agent that took part, and present only when a handover "
            "split the reply. Null is a turn one agent answered whole, which is not "
            "the same as an empty list and never becomes one."
        )
    )
    asr_ms: int | None = Field(
        description=(
            "Transcription elapsed, in milliseconds. Null where none was measured this "
            "turn, and under metrics-off."
        )
    )
    first_token_ms: int | None = Field(
        description="Request to the reply's first token, in milliseconds. Null under metrics-off."
    )
    llm_ms: int | None = Field(
        description=(
            "The reply's LLM round durations summed, in milliseconds. Null under "
            "metrics-off."
        )
    )
    tts_first_audio_ms: int | None = Field(
        description=(
            "The reply's first synthesis request to its first audio bytes, in "
            "milliseconds, measured at the provider boundary and deliberately not at "
            "the device. Null when the reply spoke nothing, and under metrics-off."
        )
    )
    rounds: int | None = Field(
        description="How many LLM rounds the reply took. Null under metrics-off."
    )
    input_tokens: int | None = Field(
        description=(
            "Input tokens summed across the turn's rounds; OTel's "
            "`gen_ai.usage.input_tokens`. Null when the provider reported no usage, "
            "and under metrics-off."
        )
    )
    output_tokens: int | None = Field(
        description=(
            "Output tokens summed across the turn's rounds; OTel's "
            "`gen_ai.usage.output_tokens`. Null when the provider reported no usage, "
            "and under metrics-off."
        )
    )
    tool_calls: int = Field(
        description=(
            "How many calls this turn issued, which is how many entries the list below "
            "holds. Structural rather than telemetry: it survives both switches."
        )
    )
    tool_invocations: list[ToolInvocation] = Field(
        description="The calls the reply made, in the order the model issued them."
    )


class ConversationSummary(BaseModel):
    """One session as the listing shows it."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(
        description=(
            "The session's monotonic row id, never reused. It is this listing's "
            "cursor: a page asked for with it holds the sessions before it."
        )
    )
    session: str = Field(
        description=(
            "The session's uuid hex, which addresses the two reads below and names the "
            "capture triplet of the same conversation."
        )
    )
    device: str | None = Field(
        description=(
            "The device's MAC in canonical form, and null when the session was "
            "rejected before one was understood."
        )
    )
    agent: str | None = Field(
        description="The agent the session opened with, before any handover."
    )
    started_at: str = Field(description="When the session opened, as an ISO-8601 instant in UTC.")
    closed_at: str | None = Field(
        description=(
            "When it closed, in the same form. Null in a session that is still running "
            "and in one whose close was never persisted, which a crash leaves behind."
        )
    )
    duration_s: float | None = Field(
        description="How long it lasted, in seconds. A measured number: null under metrics-off."
    )
    close_reason: CloseReason | str | None = Field(
        description=(
            "What ended it, one of `limit`, `idle`, `drain`, `client` or `error`, the "
            "first cause to fire winning. Null until it closes. The five tokens are "
            "the set a server latches, and the column that holds them is deliberately "
            "unconstrained, so a token a later release adds is served as it was "
            "stored: a read that refused one would drop a whole page over one row, "
            "the same reason the database refuses none."
        )
    )
    turns: int = Field(description="How many turns this session holds.")


class ConversationList(BaseModel):
    """One page of the session listing, newest first."""

    model_config = ConfigDict(extra="forbid")

    items: list[ConversationSummary] = Field(
        description="The sessions on this page, newest first."
    )
    next_cursor: int | None = Field(
        description=(
            "What to send as `cursor` for the page after this one, and null when this "
            "was the last: it is the id of the last item here, and the next page holds "
            "the sessions below it. Null means there is nothing further right now, not "
            "that there never will be; a listing re-read later starts from the top."
        )
    )


class ConversationDetail(BaseModel):
    """One session, whole: its row and what hangs off it."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(description="The session's monotonic row id, the listing's cursor.")
    session: str = Field(description="The session's uuid hex.")
    device: str | None = Field(
        description="The device's MAC in canonical form, or null when none was understood."
    )
    client: str | None = Field(
        description="The client identifier the device announced, when it announced one."
    )
    agent: str | None = Field(
        description="The agent the session opened with, before any handover."
    )
    agents: list[str] | None = Field(
        description=(
            "Every agent the device is bound to, by name, as the binding resolved at "
            "open. The first is the agent the session started on."
        )
    )
    protocol: str | None = Field(
        description="The device protocol version this session negotiated."
    )
    started_at: str = Field(description="When the session opened, as an ISO-8601 instant in UTC.")
    closed_at: str | None = Field(description="When it closed, in the same form, or null.")
    duration_s: float | None = Field(
        description="How long it lasted, in seconds. Null under metrics-off."
    )
    close_reason: CloseReason | str | None = Field(
        description=(
            "What ended it, one of the five tokens the listing describes, or null "
            "until it closes."
        )
    )
    server_version: str | None = Field(
        description="The server version that recorded this session."
    )
    revision: str | None = Field(description="The build revision that recorded it.")
    providers: dict[str, Any] | None = Field(
        description=(
            "The resolved provider entry per pipeline stage, the same structure the "
            "capture manifest carries. It holds environment variable names, never "
            "credentials."
        )
    )
    metrics: bool = Field(
        description=(
            "Whether metrics storage was on for this session, so a null number is "
            "distinguishable from a number that was never stored."
        )
    )
    text: bool = Field(
        description=(
            "Whether text storage was on for this session, so a null utterance is "
            "distinguishable from an utterance that was never stored."
        )
    )
    dropped: int = Field(
        description=(
            "Records this session lost: events refused at the writer's in-flight bound, "
            "and anything a failed transaction rolled back. The store recording its own "
            "incompleteness, the way the capture manifest records `complete`."
        )
    )
    turns: int = Field(description="How many turns this session holds.")
    events: int = Field(
        description=(
            "How many events rows it holds: the decision track, `session_open` through "
            "`session_closed`. Zero under metrics-off, where no events row lands. They "
            "are deliberately not served over REST; the database is that surface."
        )
    )


class ConversationTurns(BaseModel):
    """One page of a session's timeline, oldest first."""

    model_config = ConfigDict(extra="forbid")

    items: list[ConversationTurn] = Field(
        description="The turns on this page, ascending by id, which is chronological."
    )
    next_cursor: int | None = Field(
        description=(
            "What to send as `cursor` for the page after this one, and null when this "
            "was the last. The next page holds the turns after it, which is also how a "
            "client that read up to a turn asks for what has happened since."
        )
    )


# The query arguments
#
# Taken as strings and parsed here rather than declared as integers.
# FastAPI's own validation answers with the sanitized body-shaped
# sentence this API substitutes for it, which would be the wrong
# sentence for a query argument, and its default document shape echoes
# the value it rejected. What each argument has to be is said in its
# description and enforced below, with a refusal that names the rule.

DeviceQuery = Annotated[
    str | None,
    Query(
        description=(
            "Only the sessions of this device, by MAC. Colons or dashes, upper or "
            "lower case; it is normalized before it is matched. Anything that is not a "
            "MAC address is refused."
        )
    ),
]

LimitQuery = Annotated[
    str | None,
    Query(
        description=(
            f"How many rows this page may hold: a whole number from 1 to {LIMIT_MAX}, "
            f"defaulting to {LIMIT_DEFAULT}. Anything else is refused."
        )
    ),
]

CursorQuery = Annotated[
    str | None,
    Query(
        description=(
            "Where to carry on from: a row id this API answered with, as a whole "
            "number. Absent means the first page. Anything else is refused."
        )
    ),
]


def reader(database: DatabaseConfig) -> Callable[[], Iterator[Connection]]:
    """Per-request access to the store: open a connection, yield it,
    dispose the engine.

    Nothing holds an engine between requests, so a store restored from a
    backup underneath a running server is met as it is now rather than
    through a connection pooled before it moved.

    This was the shape the configuration database's dependency had too,
    until #142 gave that one a single lifespan-owned engine. This one is
    deliberately left as it is, which that plan records as considered
    and declined: the property above is this store's own, and a
    configuration row is not restored from a backup underneath a running
    server the way a month of conversations might be. Pooling it is an
    optimization with risks of its own, recorded as a follow-up rather
    than smuggled into the cutover (#283).
    """

    def open_reader() -> Iterator[Connection]:
        engine = read_engine(database)
        try:
            with engine.connect() as connection:
                yield connection
        finally:
            engine.dispose()

    return open_reader


def _reader(request: Request) -> Iterator[Connection]:
    """The store, for the length of one request.

    Taken from the application rather than closed over by the routes, so
    that the document can be rendered from an application built without
    a database to reach: `build_api` attaches the dependency and
    `document()` never resolves it.
    """
    runtime: ApiRuntime = request.app.state.api_runtime
    yield from runtime.conversations()


ReaderDep = Annotated[Connection, Depends(_reader)]


def routes(api: FastAPI, problems: Callable[..., dict[int | str, dict[str, Any]]]) -> None:
    """The three reads, registered on the application that is both
    mounted and rendered.

    `problems` is `config/api.py`'s own describer, passed in rather than
    imported: that module imports this one to register these routes, and
    an import back would be a cycle. What it produces is the same
    `Problem` body every other refusal on this API answers with.

    The handlers are plain `def`, so FastAPI runs them on the threadpool
    and the synchronous database work never blocks the event loop, which
    is what the repository's reads already do.
    """

    @api.get(
        "/conversations",
        response_model=ConversationList,
        responses=problems(401, 404, 422, 500, instead=PROBLEMS_INSTEAD),
    )
    def read_conversations(
        reader: ReaderDep,
        device: DeviceQuery = None,
        limit: LimitQuery = None,
        cursor: CursorQuery = None,
    ) -> dict[str, Any]:
        """The sessions this deployment recorded, newest first.

        Filtered by `device` when given, and paginated with `cursor`,
        which holds the sessions below it, so a client walks back through
        the record one page at a time.
        """
        size = _limit(limit)
        criteria: list[ColumnElement[bool]] = []
        mac = _device(device)
        if mac is not None:
            criteria.append(sessions.c.device == mac)
        below = _cursor(cursor)
        if below is not None:
            criteria.append(sessions.c.id < below)
        # A correlated count rather than a join, so a session with no
        # turns is still a row and the page size still counts sessions.
        counted = (
            select(func.count())
            .select_from(turns)
            .where(turns.c.session == sessions.c.session)
            .scalar_subquery()
        )
        found = _rows(
            reader,
            select(*SUMMARY_COLUMNS, counted.label("turns"))
            .where(*criteria)
            .order_by(sessions.c.id.desc())
            .limit(size + 1),
        )
        return _page(found, size)

    @api.get(
        "/conversations/{session}",
        response_model=ConversationDetail,
        responses=problems(401, 404, 422, 500, instead=PROBLEMS_INSTEAD),
    )
    def read_conversation(session: str, reader: ReaderDep) -> dict[str, Any]:
        """One session, whole: every column of its row, with how many
        turns and events hang off it.

        The two counts are read in the same transaction as the row, so
        they describe one snapshot rather than three moments of a
        session that may still be running.
        """
        row = _session(reader, session)
        return row | {
            "turns": _count(reader, turns, session),
            "events": _count(reader, events, session),
        }

    @api.get(
        "/conversations/{session}/turns",
        response_model=ConversationTurns,
        responses=problems(401, 404, 422, 500, instead=PROBLEMS_INSTEAD),
    )
    def read_conversation_turns(
        session: str,
        reader: ReaderDep,
        limit: LimitQuery = None,
        cursor: CursorQuery = None,
    ) -> dict[str, Any]:
        """One session's timeline, oldest first, each turn carrying the
        calls it made.

        `cursor` holds the turns strictly after it, which is the
        direction a client reconciling what it has already read asks in:
        the ids are monotonic and never reused, so what came after one is
        a stable question.
        """
        size = _limit(limit)
        after = _cursor(cursor)
        # Before the page, so an unknown session is a 404 rather than an
        # empty timeline that reads like a session with nothing in it.
        _session(reader, session)
        criteria: list[ColumnElement[bool]] = [turns.c.session == session]
        if after is not None:
            criteria.append(turns.c.id > after)
        found = _rows(
            reader,
            select(*TURN_COLUMNS)
            .where(*criteria)
            .order_by(turns.c.id)
            .limit(size + 1),
        )
        page = _page(found, size)
        _nest_invocations(reader, page["items"])
        return page


def _rows(reader: Connection, query: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in reader.execute(query).mappings()]


def _page(found: list[dict[str, Any]], size: int) -> dict[str, Any]:
    """One page and the cursor after it, from one row more than the page
    holds.

    The extra row is what makes `next_cursor` honest without a second
    count: it is null exactly when there was nothing beyond this page at
    the moment it was read.
    """
    items = found[:size]
    return {
        "items": items,
        "next_cursor": items[-1]["id"] if len(found) > size else None,
    }


def _nest_invocations(reader: Connection, items: list[dict[str, Any]]) -> None:
    """The calls of every turn on the page, in the order the model
    issued them, nested under the turn that issued them.

    One statement for the whole page rather than one per turn: a page
    holds at most `LIMIT_MAX` turns, which is well inside what a driver
    takes bound into an `IN`.
    """
    grouped: dict[int, list[dict[str, Any]]] = {turn["id"]: [] for turn in items}
    if grouped:
        for call in _rows(
            reader,
            select(*INVOCATION_COLUMNS, tool_invocations.c.turn)
            .where(tool_invocations.c.turn.in_(list(grouped)))
            .order_by(tool_invocations.c.turn, tool_invocations.c.position),
        ):
            grouped[call.pop("turn")].append(call)
    for turn in items:
        turn["tool_invocations"] = grouped[turn["id"]]


def _session(reader: Connection, session: str) -> dict[str, Any]:
    """One session row, or the refusal that says where to look instead.

    The id is not repeated in the message: it arrived in the path, and
    what a caller needs is the reason a row it expected is not there.
    """
    found = _rows(reader, select(sessions).where(sessions.c.session == session))
    if not found:
        raise UnknownEntityError(_UNKNOWN_SESSION)
    return found[0]


def _count(reader: Connection, table: Table, session: str) -> int:
    return reader.execute(
        select(func.count()).select_from(table).where(table.c.session == session)
    ).scalar_one()


def _limit(value: str | None) -> int:
    number = _whole(value)
    if value is not None and (number is None or not 1 <= number <= LIMIT_MAX):
        raise ConfigError(_LIMIT_REFUSED)
    return LIMIT_DEFAULT if number is None else number


def _cursor(value: str | None) -> int | None:
    number = _whole(value)
    if value is not None and (number is None or number > MAX_ROW_ID):
        raise ConfigError(_CURSOR_REFUSED)
    return number


def _whole(value: str | None) -> int | None:
    """A non-negative whole number, or None for anything else, the
    absent argument included.

    `isdigit` rather than a bare `int()`: that accepts a sign, an
    underscore and digits outside ASCII, and what these arguments are is
    a row id or a count, which none of those spellings is. Bounded in
    length before it is converted, because `int` on a very long string
    is work a caller should not be able to ask for.
    """
    if value is None or len(value) > 19 or not (value.isascii() and value.isdigit()):
        return None
    return int(value)


def _device(value: str | None) -> str | None:
    """The device filter, normalized the way every other MAC in this
    project is, so `AA-BB-...` and `aa:bb:...` reach the same sessions.

    Refused rather than matched literally when it is not a MAC: an empty
    page would be a true answer to a question the caller did not mean to
    ask.
    """
    if value is None:
        return None
    problem: ConfigError | None = None
    try:
        return normalize_mac(value)
    except ValueError:
        # Built here and raised outside the arm, the rule this codebase
        # settled on: a refusal raised inside the arm carries whatever
        # the caught exception held as its `__context__`. What
        # `normalize_mac` holds is a fixed sentence now (#205), and the
        # shape stays because the rule is the repository's rather than
        # that one validator's.
        problem = ConfigError(_DEVICE_REFUSED)
    raise problem


__all__ = [
    "LIMIT_DEFAULT",
    "LIMIT_MAX",
    "CloseReason",
    "ConversationDetail",
    "ConversationList",
    "ConversationSummary",
    "ConversationTurn",
    "ConversationTurns",
    "ToolInvocation",
    "ToolSource",
    "TurnLeg",
    "reader",
    "routes",
]
