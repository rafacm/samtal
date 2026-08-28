"""The conversation store on the gated /api: two namespaces over one set
of rows.

`/sessions` answers connection episodes and `/conversations` answers
durable threads, and they are two readings of the same turns rather than
two stores: a turn names both, so one session's turns can belong to
several threads and one thread's turns can come from several sessions.
Three reads and two erasures each, with the erasures asymmetric on
purpose (see `threads.erase_conversations`).

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

- **A cursor is plain values a caller can read, never an encoding to
  version.** Three of the four listings page on the monotonic row ids
  alone: the session list backwards (`id` strictly below the cursor,
  newest first) and both timelines forwards (`id` strictly after it),
  which is the reconcile direction. The thread listing cannot, because
  it orders on activity and activity moves, so it pages on the pair
  (`last_active_at`, `id`) spelled out as two named parameters,
  `cursor_active` and `cursor_id`. Two values rather than one opaque
  blob is what keeps the rule: what a caller sends back is what it was
  answered with, and there is nothing here a later release has to go on
  decoding.
- **A refused argument is never quoted back.** A limit, a cursor, a
  device or a day that cannot be read answers with a fixed sentence
  describing what the argument has to be, and so does a purge that
  named no selector at all. What arrived is the caller's, and a refusal
  that echoed it would be the one place this API prints something it
  was handed. The rule is not weaker on the erasures: a driver
  exception carries the statement it failed on and the connection
  string it failed over, so which sentence a failed deletion answers is
  decided by the db classifier's closed set and the exception is
  dropped rather than read.
- **A store with no rows answers its ordinary empty shapes.** There is
  no 404 for "this deployment never recorded" any more, and that is a
  deliberate contract change (#283): the distinction it drew was
  between a file that existed and one that did not, and there is no
  file. Boot migrates the schema whether or not recording is on, so
  what a deployment that never recorded has is empty tables, and an
  empty list is the honest answer to a question about them. The 404s
  that remain are the two about an id that was addressed.

Every read opens a connection for the length of one request through
`db.read_engine`: no migration, no advisory lock, and a repeatable-read
snapshot that cannot write. Every erasure opens a write engine for the
length of one transaction through `db.write_engine`, which takes the
chain's advisory lock at BEGIN and migrates nothing. Neither holds an
engine between requests, and both work with recording off, where there
is no `ConversationStore` and no engine to borrow.
"""

import datetime as dt
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, Query, Request
from sqlalchemy import ColumnElement, Connection, Table, func, select

from vinga_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    StorageError,
    UnknownEntityError,
)
from vinga_server.config.models import DatabaseConfig, normalize_mac
from vinga_server.config.responses import (
    CloseReason,
    ConversationDetail,
    ConversationList,
    ConversationSummary,
    ConversationTurn,
    ConversationTurns,
    Erasure,
    SessionDetail,
    SessionList,
    SessionSummary,
    SessionTurn,
    SessionTurns,
    ThreadErasure,
    ToolInvocation,
    ToolSource,
    TurnLeg,
)
from vinga_server.conversations import store, threads
from vinga_server.conversations.schema import (
    events,
    sessions,
    tool_invocations,
    turns,
)
from vinga_server.conversations.store import CONVERSATIONS_CHAIN
from vinga_server.db import is_busy, read_engine, write_engine

if TYPE_CHECKING:
    # The name only, for the annotation in `_reader`: the configuration
    # API imports this module to mount these reads, so a module-scope
    # import in this direction would not load. Nothing here runs at
    # runtime.
    from vinga_server.config.api import ApiRuntime

# Every transport shape these routes answer is declared in
# `config/responses.py` and imported back here. Two surfaces know them
# and only one may pay for FastAPI: `vinga session show` and
# `vinga conversation show` read an answer by validating it against the
# shape the API said it would send, and a CLI that imported this module
# to find out would import FastAPI, SQLAlchemy and the whole store with
# it. The closed sets those shapes carry (the close reasons, the tool
# sources) are written out there for the same reason, and held equal to
# the schema's own tuples by the pin in `test_api_openapi.py`.

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

# The keyset cursor's refusal, which names both parameters because the
# rule is about the pair rather than about either one. Half a pair is a
# caller that dropped an argument, and answering it with the first page
# would be answering a question it did not ask.
_CURSOR_PAIR_REFUSED = (
    "cursor_active and cursor_id come together or not at all, and both have to be "
    "values this API answered with: cursor_active is a last_active_at, written as an "
    "ISO-8601 instant, and cursor_id is the row id beside it. Absent means the first "
    "page. What was sent is not quoted back"
)

# How long the activity half of a cursor may be before it is refused
# without being parsed. An ISO-8601 instant with microseconds and an
# offset is 32 characters; the margin is for the spellings a caller may
# legally write it in, and the bound is here so that parsing is never
# work an unbounded string can ask for.
_INSTANT_LENGTH = 40

_DEVICE_REFUSED = (
    "device has to be a MAC address: six colon-separated or dash-separated hex "
    "pairs, for example aa:bb:cc:dd:ee:ff. What was sent is not quoted back"
)

_BEFORE_REFUSED = (
    "before has to be a calendar day in UTC, written as YYYY-MM-DD, and it selects "
    "the sessions that began strictly before it. What was sent is not quoted back"
)

# What a purge with no selector is told. A refusal rather than a
# deletion of everything, because a query string that lost its arguments
# to a shell, a proxy or a typo would otherwise erase the whole store,
# and there is no undo behind it.
_NO_SELECTOR = (
    "a purge names what it erases: give at least one of session, device or before, "
    "and several are combined so that every one of them has to match. Erasing "
    "everything is deliberately not something this endpoint can be asked for"
)

# What a deletion answers when the database refuses it. Two sentences,
# chosen by the db classifier's closed set and not by anything the
# exception says, because a driver's own words carry the statement it
# failed on and the connection string it failed over.
_ERASURE_BUSY = (
    "the conversation store's write lock is held by another writer, and nothing was "
    "deleted. The same request may be made again"
)

_ERASURE_FAILED = (
    "the conversation store could not be written, and nothing was deleted. The "
    "details are in the server's log"
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

# And the other one, which says the same thing about the other
# projection: what the id is, and the two ways a thread stops being
# there. Neither names what was asked for.
_UNKNOWN_CONVERSATION = (
    "no conversation of that id is in the conversation store. The id is the thread's "
    "uuid hex, which every turn of it carries; a thread whose last activity is older "
    "than server.conversations.retention_days has been pruned, and a thread that lost "
    "every turn to an erasure was deleted with them."
)

# What each refusal means here, where the shared sentence would not be
# true. 404 is two cases and the status alone cannot tell them apart;
# 422 is never about addressing, since the only things these routes
# parse are a limit, a cursor, a device, a day and the selector rule;
# 409 is the conversation store's write lock rather than the
# configuration database's; and 500 is about the conversation store
# rather than the stored configuration.
PROBLEMS_INSTEAD: dict[int, str] = {
    404: "No session of that id is in the conversation store.",
    409: (
        "The conversation store's write lock is held by another writer. Nothing was "
        "deleted, and the same request may be made again."
    ),
    422: (
        "One of the query arguments could not be read: the limit, the cursor, the "
        "device filter or the day, or a purge named no selector at all. Nothing sent "
        "is quoted back."
    ),
    500: (
        "The conversation store cannot be read or written, or the request failed for "
        "a reason that is not the caller's. The details are in the server's log."
    ),
}

# The same, for the thread namespace. A second table rather than one
# with both sentences in it: what a status means here is about a
# conversation, and a shared 404 saying "a session or a conversation"
# would be less true on both resources than either sentence is on its
# own.
THREAD_PROBLEMS_INSTEAD: dict[int, str] = {
    404: "No conversation of that id is in the conversation store.",
    409: (
        "The conversation store's write lock is held by another writer. Nothing was "
        "deleted, and the same request may be made again."
    ),
    422: (
        "One of the query arguments could not be read: the limit, the agent filter, "
        "or the two halves of the listing's cursor, which come together or not at "
        "all. Nothing sent is quoted back."
    ),
    500: (
        "The conversation store cannot be read or written, or the request failed for "
        "a reason that is not the caller's. The details are in the server's log."
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

SessionQuery = Annotated[
    str | None,
    Query(
        description=(
            "Only this session, by its uuid hex. The same erasure the addressed form "
            "of this resource performs, offered here so that one selector grammar "
            "covers every purge."
        )
    ),
]

AgentQuery = Annotated[
    str | None,
    Query(
        description=(
            "Only the threads of this agent, by the name the configuration gives it. "
            "Matched exactly and not refused for shape: an agent name is a word this "
            "deployment chose, and a name nothing answers to is an empty page rather "
            "than a mistake this API can recognize."
        )
    ),
]

CursorActiveQuery = Annotated[
    str | None,
    Query(
        description=(
            "The activity half of where to carry on from: the `last_active_at` of the "
            "last thread on the previous page, as an ISO-8601 instant. Sent together "
            "with `cursor_id` or not at all; absent means the first page."
        )
    ),
]

CursorIdQuery = Annotated[
    str | None,
    Query(
        description=(
            "The id half of the same cursor: the `id` of that thread, as a whole "
            "number. The page holds the threads strictly below the pair, which is "
            "what makes the order total where two threads share an activity stamp."
        )
    ),
]

BeforeQuery = Annotated[
    str | None,
    Query(
        description=(
            "Only the sessions that began strictly before this UTC day, written as "
            "YYYY-MM-DD. A session that began at any moment of the named day is not "
            "selected, which is what 'before the fifteenth' means. Anything else is "
            "refused."
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


@contextmanager
def erasing(database: DatabaseConfig) -> Iterator[Connection]:
    """One deletion's transaction, on a write engine opened for it and
    disposed after it.

    Opened per request for the reason a read is, and for one more that
    is this endpoint's own: erasure has to work with recording off,
    where no `ConversationStore` exists and there is no long-lived
    engine to borrow. `write_engine` rather than `open_conversations`,
    because a deletion migrates nothing: boot owns the schema, and a
    request that ran Alembic would be a request that could change the
    shape of the store it was asked to delete a row from.

    The chain's advisory lock is taken at BEGIN, so this transaction and
    the writer's markers are ordered rather than interleaved: a session
    the writer is still talking on is deleted whole or not at all, and
    the writer meets the absence at its next marker, which is the
    tombstone rule it already has.
    """
    engine = write_engine(database, CONVERSATIONS_CHAIN)
    try:
        with engine.begin() as connection:
            yield connection
    finally:
        engine.dispose()


def _eraser(request: Request) -> Callable[[], AbstractContextManager[Connection]]:
    """How to open one deletion's transaction, taken off the
    application's runtime like the reader beside it.

    The factory rather than the transaction, deliberately. A dependency
    that yielded an open transaction would put its commit and its
    rollback in the framework's hands, and what a refusal must not do
    here is leave half a deletion behind; entered inside the handler,
    the `with` and the arm that classifies its failure are the same
    piece of code.
    """
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.erasures


EraserDep = Annotated[
    Callable[[], AbstractContextManager[Connection]], Depends(_eraser)
]


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
    """The three reads and the two erasures, registered on the
    application that is both mounted and rendered.

    `problems` is `config/api.py`'s own describer, passed in rather than
    imported: that module imports this one to register these routes, and
    an import back would be a cycle. What it produces is the same
    `Problem` body every other refusal on this API answers with.

    The handlers are plain `def`, so FastAPI runs them on the threadpool
    and the synchronous database work never blocks the event loop, which
    is what the repository's reads already do.

    The two erasures overlap deliberately. The addressed form is what
    the noun grammar and a user interface want, and the selector form is
    the purge whose three selectors were settled by the command it
    replaces (#282); both do exactly the same thing through the same
    helper in `threads.py`, which is what keeps them one bookkeeping
    path rather than two.
    """

    @api.get(
        "/sessions",
        response_model=SessionList,
        responses=problems(401, 404, 422, 500, instead=PROBLEMS_INSTEAD),
    )
    def read_sessions(
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
        "/sessions/{session}",
        response_model=SessionDetail,
        responses=problems(401, 404, 422, 500, instead=PROBLEMS_INSTEAD),
    )
    def read_session(session: str, reader: ReaderDep) -> dict[str, Any]:
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
        "/sessions/{session}/turns",
        response_model=SessionTurns,
        responses=problems(401, 404, 422, 500, instead=PROBLEMS_INSTEAD),
    )
    def read_session_turns(
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

    @api.delete(
        "/sessions/{session}",
        response_model=Erasure,
        responses=problems(401, 404, 409, 422, 500, instead=PROBLEMS_INSTEAD),
    )
    def erase_session(session: str, erase: EraserDep) -> dict[str, int]:
        """Erase one named session: its row, its turns wherever their
        threads are, the calls those turns made, and its events.

        Erasure outranks every copy the store derived from what is going
        (`threads.erase_sessions` says exactly which), and it is one
        transaction, so a session's rows go together or none of them
        does. A session that is still running when its row goes stops
        being recorded at the writer's next marker; what it says after
        that is not kept.
        """
        return _erased(erase, session=session, addressed=True)

    @api.delete(
        "/sessions",
        response_model=Erasure,
        responses=problems(401, 409, 422, 500, instead=PROBLEMS_INSTEAD),
    )
    def purge_sessions(
        erase: EraserDep,
        session: SessionQuery = None,
        device: DeviceQuery = None,
        before: BeforeQuery = None,
    ) -> dict[str, int]:
        """Erase every session the selectors name, in one transaction.

        At least one selector is required and several are combined with
        AND, so a purge always names less than everything. The semantics
        are the retired `conversations purge` command's, carried over
        rather than reopened: this is that command, as an act of the API
        with the CLI in front of it.
        """
        if session is None and device is None and before is None:
            raise ConfigError(_NO_SELECTOR)
        return _erased(
            erase, session=session, device=_device(device), before=_before(before)
        )

    @api.get(
        "/conversations",
        response_model=ConversationList,
        responses=problems(401, 422, 500, instead=THREAD_PROBLEMS_INSTEAD),
    )
    def read_conversations(
        reader: ReaderDep,
        agent: AgentQuery = None,
        limit: LimitQuery = None,
        cursor_active: CursorActiveQuery = None,
        cursor_id: CursorIdQuery = None,
    ) -> dict[str, Any]:
        """The threads this deployment recorded, most recently active
        first.

        Filtered by `agent` when given, and paginated with the pair
        `cursor_active` and `cursor_id`, which holds the threads below
        it. Activity moves, so the semantics under concurrent recording
        are stated rather than implied: a thread spoken to between two
        pages moves ahead of the cursor and is missed by that pass,
        appearing at the head of a fresh one, and a page boundary never
        duplicates or skips a thread whose pair did not move.
        """
        size = _limit(limit)
        found = threads.listed(
            reader,
            agent=agent,
            limit=size + 1,
            cursor=_keyset(cursor_active, cursor_id),
        )
        return _keyset_page(found, size)

    @api.get(
        "/conversations/{conversation}",
        response_model=ConversationDetail,
        responses=problems(401, 404, 422, 500, instead=THREAD_PROBLEMS_INSTEAD),
    )
    def read_conversation(conversation: str, reader: ReaderDep) -> dict[str, Any]:
        """One thread, whole: its row, how many turns it holds across
        every session, and how many recap checkpoints hang off it.

        The counts are read in the same transaction as the row, so they
        describe one snapshot rather than three moments of a thread that
        may still be being spoken to.
        """
        return _thread(reader, conversation)

    @api.get(
        "/conversations/{conversation}/turns",
        response_model=ConversationTurns,
        responses=problems(401, 404, 422, 500, instead=THREAD_PROBLEMS_INSTEAD),
    )
    def read_conversation_turns(
        conversation: str,
        reader: ReaderDep,
        limit: LimitQuery = None,
        cursor: CursorQuery = None,
    ) -> dict[str, Any]:
        """One thread's dialogue, oldest first, each turn carrying the
        calls it made and the session it was spoken in.

        The row id cursor, not the pair the listing takes: a turn's id
        never moves, so `cursor` holds the turns strictly after it and
        the direction is the one a client reconciling what it has
        already read asks in.
        """
        size = _limit(limit)
        after = _cursor(cursor)
        # Before the page, so an unknown thread is a 404 rather than an
        # empty dialogue that reads like a thread with nothing in it.
        _thread(reader, conversation)
        page = _page(threads.dialogue(reader, conversation, after=after, limit=size + 1), size)
        _nest_invocations(reader, page["items"])
        return page

    @api.delete(
        "/conversations/{conversation}",
        response_model=ThreadErasure,
        responses=problems(401, 404, 409, 422, 500, instead=THREAD_PROBLEMS_INSTEAD),
    )
    def erase_conversation(conversation: str, erase: EraserDep) -> dict[str, int]:
        """Erase one named thread: its row, its turns out of whatever
        sessions they were spoken in, the calls those turns made, and
        its recap checkpoints.

        The sessions themselves are untouched, and so is their
        telemetry: a session is a connection episode and it still
        happened, with a gap in it now. A thread that is still being
        spoken to when its row goes stops being recorded at the writer's
        next marker, and the conversation in the room carries on.
        """
        taken = _erasure(erase, lambda connection: _thread_erasure(connection, conversation))
        return {
            "conversations": len(taken.threads),
            "turns": taken.turns,
            "tool_invocations": taken.tool_invocations,
            "milestones": taken.milestones,
        }


def _erased(
    erase: Callable[[], AbstractContextManager[Connection]],
    session: str | None = None,
    device: str | None = None,
    before: str | None = None,
    addressed: bool = False,
) -> dict[str, int]:
    """One session deletion, whatever addressed it.

    Both session endpoints land here, which is what makes the overlap
    between them an overlap rather than a second bookkeeping path: the
    selectors are resolved to a list of sessions and the same helper
    erases them.
    """
    taken = _erasure(
        erase, lambda connection: _session_erasure(connection, session, device, before, addressed)
    )
    return {
        "sessions": taken.sessions,
        "turns": taken.turns,
        "tool_invocations": taken.tool_invocations,
        "events": taken.events,
        "conversations": len(taken.threads),
        "milestones": taken.milestones,
    }


def _erasure(
    erase: Callable[[], AbstractContextManager[Connection]],
    deleting: Callable[[Connection], threads.Erased],
) -> threads.Erased:
    """One deletion's transaction, whatever it deletes.

    The failure arms are the whole no-leak surface of a write, and they
    are here once for both namespaces. A driver exception carries the
    statement it failed on and the connection string it failed over, so
    which sentence is answered is decided by the db classifier's closed
    set and the exception itself is dropped: built inside the arm,
    raised outside it, so nothing walking the chain finds it either.

    Telling the writer happens in here, and where it happens is the
    ordering the dead-id rule needs, stated in three lines because three
    reviews have now circled this seam:

    - after the commit, never inside the transaction. A deletion that
      published and then rolled back would leave a thread that still
      exists marked dead for the rest of the process, so its later turns
      would be discarded with false acknowledgements while its rows sat
      there. A refusal has to leave the thread exactly as it was.
    - inside `store.erasure_order()`, which is entered before the
      transaction is opened and left after the publication. The commit
      releases the chain's advisory lock, and without this a writer could
      take that lock in the instant before the ids were published, find
      nothing, and write a turn onto the thread just deleted.
    - and the writer keeps the other side of the same order: it reads
      what was published inside its own durable transaction, on every
      attempt (`store._discard_dead`).
    """
    problem: ConfigError | None = None
    try:
        with store.erasure_order():
            with erase() as connection:
                taken = deleting(connection)
            store.erased(taken.threads)
    except ConfigError:
        raise
    except Exception as exc:  # noqa: BLE001 - the driver's own words never travel
        problem = DatabaseBusyError(_ERASURE_BUSY) if is_busy(exc) else StorageError(
            _ERASURE_FAILED
        )
    if problem is not None:
        raise problem
    return taken


def _session_erasure(
    connection: Connection,
    session: str | None,
    device: str | None,
    before: str | None,
    addressed: bool,
) -> threads.Erased:
    named = threads.selected(connection, session=session, device=device, before=before)
    if addressed and not named:
        # Addressed and not there, which is a 404 rather than an erasure
        # of nothing: a caller that named one session meant that session.
        raise UnknownEntityError(_UNKNOWN_SESSION)
    return threads.erase_sessions(connection, named)


def _thread_erasure(connection: Connection, conversation: str) -> threads.Erased:
    taken = threads.erase_conversations(connection, [conversation])
    if not taken.threads:
        raise UnknownEntityError(_UNKNOWN_CONVERSATION)
    return taken


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


def _keyset_page(found: list[dict[str, Any]], size: int) -> dict[str, Any]:
    """One page of the thread listing and the pair after it.

    The same one-row-more trick the row-id pages use, answering two
    values instead of one: they are null together exactly when there was
    nothing beyond this page at the moment it was read, and a caller
    sends both back or neither, which is the rule the request side
    holds to as well.
    """
    items = found[:size]
    more = len(found) > size
    return {
        "items": items,
        "next_cursor_active": items[-1]["last_active_at"] if more else None,
        "next_cursor_id": items[-1]["id"] if more else None,
    }


def _thread(reader: Connection, conversation: str) -> dict[str, Any]:
    """One thread's row with its counts, or the refusal that says where
    to look instead. The id arrived in the path and is not repeated."""
    found = threads.detail(reader, conversation)
    if found is None:
        raise UnknownEntityError(_UNKNOWN_CONVERSATION)
    return found


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


def _keyset(active: str | None, row: str | None) -> tuple[str, int] | None:
    """The thread listing's cursor, as the pair the ordering is over, or
    None for the first page.

    The two parameters come together or not at all, and half a pair is
    refused rather than half-honored: a caller that meant the first page
    sends neither, so one argument on its own is an argument that went
    missing, and answering it with the top of the listing would silently
    replay a page it had already read.
    """
    if active is None and row is None:
        return None
    number = _whole(row)
    moment = _instant(active) if active is not None else None
    if moment is None or number is None or number > MAX_ROW_ID:
        raise ConfigError(_CURSOR_PAIR_REFUSED)
    return (moment, number)


def _instant(value: str) -> str | None:
    """The activity half of a cursor, canonicalized, or None for
    anything that is not an instant.

    Canonicalized rather than compared as written, because the
    comparison is lexicographic on text and only agrees with time when
    both sides are spelled the way the writer spells them. A value this
    API answered round-trips to itself; one written with `Z` or at
    another offset is brought to the spelling the rows carry instead of
    quietly paging from the wrong place.

    Bounded before it is parsed, because parsing is work no caller
    should be able to ask an unbounded amount of.
    """
    if len(value) > _INSTANT_LENGTH:
        return None
    try:
        moment = dt.datetime.fromisoformat(value)
    except ValueError:
        # Answered rather than raised from inside the arm: `fromisoformat`
        # puts the string it could not read into its own message.
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(dt.UTC)
    return moment.isoformat()


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


def _before(value: str | None) -> str | None:
    """The day selector, as the instant the store's timestamps compare
    against.

    A calendar day in, midnight UTC out, and the comparison is strict,
    so a session that began at any moment of the named day survives.
    Rendered with `isoformat` because that is what wrote every
    `started_at` in the table, and the comparison is lexicographic on
    text: two strings written the same way by the same function compare
    chronologically, which is the property the retention pass already
    leans on.
    """
    if value is None:
        return None
    problem: ConfigError | None = None
    day = dt.date.min
    # `fromisoformat` also takes `20260815` and `2026-W33-1`, which are
    # days nobody means to type here and which would make the accepted
    # spelling wider than the one documented. Held to the extended form
    # before it is parsed.
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        problem = ConfigError(_BEFORE_REFUSED)
    try:
        day = dt.date.fromisoformat(value) if problem is None else day
    except ValueError:
        # Built here and raised outside the arm: `fromisoformat` puts
        # the string it could not read into its own message.
        problem = ConfigError(_BEFORE_REFUSED)
    if problem is not None:
        raise problem
    return dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC).isoformat()


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
    "Erasure",
    "SessionDetail",
    "SessionList",
    "SessionSummary",
    "SessionTurn",
    "SessionTurns",
    "ThreadErasure",
    "ToolInvocation",
    "ToolSource",
    "TurnLeg",
    "erasing",
    "reader",
    "routes",
]
