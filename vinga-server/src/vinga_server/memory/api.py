"""What this deployment remembers, on the gated /api: three scopes,
addressed.

The operator's door onto memory, and the audit door before it is
anything else. An agent accrues facts about the person it talks to, a
board accrues notes about the place it stands in, and a conversation
keeps a ledger of what is currently true in it. All three are content
somebody said in a room, all three are injected into a prompt, and until
this namespace existed the only way to see any of it was to read the
tables. The `memory` schema is deliberately not granted to the read-only
analyst role, so this is the surface rather than SQL.

Three questions it answers, in the order an operator asks them: who is
remembering anything at all, what is one of them remembering, and this
is wrong, take it out.

The route functions live here and are registered by `config/api.py`'s
`_application()`, exactly as the conversation record's are, so a route
cannot be served without being in the committed contract; `problems`
travels with them because the import cannot go both ways.

Nothing here decides what a scope means. `memory/store.py` owns the
tables, the caps, the held area and the index, and every statement below
is one of its own sentences on a connection this module opened.

Four transport rules the routes hold to, three of them the conversation
namespace's and one this namespace's own:

- **An owner with no rows is an empty shape, never a 404.** The #283
  contract: what a deployment that has been told nothing has is empty
  tables, and an empty listing is the honest answer about them. The
  refusals that remain are the two about something addressed: a fact
  number, and a ledger entry named in a body.
- **Nothing a caller sends is quoted back**, refusals included, and the
  values these routes are handed are exactly the ones a mistake puts a
  credential into.
- **A cursor is plain values a caller can read.** The fact listings walk
  the row ids and the three owner listings walk the owner text; both are
  what the previous page answered with, and neither is an encoding a
  later release has to keep reading.
- **Content never rides a URL.** A corrected fact and a ledger key
  travel in a request body and never in a path segment or a query
  argument. A fact is what somebody said and a key is what a model
  chose, so either can be credential-shaped, and a path reaches proxy
  and access logs where a body does not.

Every read opens a connection for the length of one request through
`db.read_engine`, and every write opens a transaction for the length of
one through `db.write_engine` on the memory chain, which takes that
chain's advisory lock at BEGIN. Neither holds an engine between
requests, and neither migrates anything: boot owns the schema.

One of these writes reaches rows a conversation's own lifecycle owns,
the ledger, so it is taken inside the erasure order the conversation
record publishes through, exactly as an erasure is. What that buys is
that this deletion and the transaction that may be erasing the same
thread are ordered rather than interleaved, so the count this answers
with is a count of rows this request took. It consults no dead-thread
set, and the reason is that the set exists to stop a write from
recreating a row for a thread that is gone: every statement this door
issues against a thread is a delete.
"""

import contextlib
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Body, Depends, FastAPI, Path, Query, Request
from sqlalchemy import Connection

from vinga_server import paging
from vinga_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    StorageError,
    UnknownEntityError,
)
from vinga_server.config.models import DatabaseConfig, normalize_mac
from vinga_server.config.responses import (
    MemoryConversation,
    MemoryConversations,
    MemoryCorrection,
    MemoryEntry,
    MemoryErasure,
    MemoryFact,
    MemoryFacts,
    MemoryOwner,
    MemoryOwners,
    MemoryState,
    MemoryStateErasure,
    MemoryStateKey,
    request_body,
)
from vinga_server.conversations.store import erasure_order
from vinga_server.db import is_busy, read_engine, write_engine
from vinga_server.memory import store
from vinga_server.memory.scopes import MemoryScope
from vinga_server.memory.store import MEMORY_CHAIN

if TYPE_CHECKING:
    # The name only, for the annotations below: the configuration API
    # imports this module to mount these routes, so a module-scope import
    # in this direction would not load.
    from vinga_server.config.api import ApiRuntime

# Every transport shape these routes answer is declared in
# `config/responses.py` and imported back here, for the reason the
# conversation namespace's are: two surfaces know them and only one may
# pay for FastAPI, since the CLI reads an answer by validating it
# against the shape the API said it would send.

# How long an owner cursor may be before it is refused without being
# used. What a caller sends back is what a page answered with, so its
# length is an owner's: a thread hex is 32 characters and a MAC is 17,
# and the margin is for an agent name, which is a word this deployment
# chose. The bound is here so that nothing unbounded is bound into a
# statement.
_OWNER_LENGTH = 512

_OWNER_CURSOR_REFUSED = (
    "cursor has to be one of the owner names this API answers with, or absent for the "
    "first page. What was sent is not quoted back"
)

# What a fact number that is not one is told.
#
# Parsed here rather than declared as an integer path parameter, for
# exactly the reason every query argument on this API is: the
# framework's own refusal for a path that will not parse is the
# body-shaped sentence this API substitutes for its validation, which
# would tell a caller who mistyped a number to send a JSON object; and a
# number past the range of the identity column is a caller's mistake
# that would otherwise reach the driver and be reported as a storage
# failure, which is a healthy database being called broken.
_NOT_A_FACT_NUMBER = (
    "a fact is addressed by the number the facts listing answers with, as a whole "
    "number. What was sent is not quoted back"
)

_NOT_A_MAC = (
    "the device in this path has to be a MAC address: six colon-separated or "
    "dash-separated hex pairs, for example aa:bb:cc:dd:ee:ff. What was sent is not "
    "quoted back"
)

# What the two bodies have to be, said as an expectation rather than as
# a complaint about what arrived. Neither is quoted back, and on these
# two that is not a nicety: a remembered fact is what somebody said in a
# room and a ledger key is a word a model chose, so either can be
# exactly the value a refusal must not repeat.
_CORRECTION_BODY = (
    'the body has to be a JSON object with exactly one key, "fact", holding the '
    "corrected text as a non-empty string. Nothing sent is quoted back"
)

_STATE_BODY = (
    'the body has to be a JSON object with exactly one key, "key", holding the name of '
    "one entry as a non-empty string, or there has to be no body at all, which clears "
    "the whole ledger. Nothing sent is quoted back"
)

# The two 404s. Neither names what the request asked for: a fact number
# arrives in the path and a key arrives in a body, and what is worth
# saying about either is where to look instead.
#
# The fact one does not distinguish a number that is not stored from one
# stored under another agent or another board, for the reason the tool's
# own refusal does not: a refusal that told them apart would confirm
# that somebody else's numbers exist.
_UNKNOWN_FACT = (
    "no fact of that number is stored under that memory. The numbers are the ids this "
    "namespace's facts listing answers with, and they are never reused, so a number "
    "that is not there is a fact that has been corrected out, erased, or was never "
    "this owner's. Nothing was changed."
)

_UNKNOWN_ENTRY = (
    "that conversation is keeping no entry under that name. The names are the keys the "
    "state read answers with, and a conversation whose thread has been erased or pruned "
    "keeps nothing at all. Nothing was changed."
)

# What a write the database refused answers with. Two sentences, chosen
# by the db classifier's closed set and not by anything the exception
# says, because a driver's own words carry the statement it ran and the
# values bound into it, which here are an owner, a fact and a key.
_MEMORY_BUSY = (
    "memory's write lock is held by another writer, and nothing was changed. The same "
    "request may be made again"
)

_MEMORY_FAILED = (
    "memory could not be written, and nothing was changed. The details are in the "
    "server's log"
)

# What each refusal means here, where the shared sentence would not be
# true. 404 is about a fact or an entry rather than about an entity of
# the stored configuration; 409 is memory's own write lock; 422 covers
# the paging arguments and the two bodies; and 500 is about memory
# rather than about the stored configuration.
MEMORY_PROBLEMS_INSTEAD: dict[int, str] = {
    404: "No fact of that number, or no entry of that name, is in this deployment's memory.",
    409: (
        "Memory's write lock is held by another writer. Nothing was changed, and the "
        "same request may be made again."
    ),
    422: (
        "The limit or the cursor could not be read, the device in the path is not a MAC "
        "address, or the body is not the shape this endpoint takes. Nothing sent is "
        "quoted back."
    ),
    500: (
        "Memory cannot be read or written, or the request failed for a reason that is "
        "not the caller's. The details are in the server's log."
    ),
}


# The query arguments
#
# Taken as strings and parsed here rather than declared as integers, for
# the reason the conversation namespace's are: FastAPI's own validation
# answers with the body-shaped sentence this API substitutes for it, and
# its default document shape echoes the value it rejected.

LimitQuery = Annotated[
    str | None,
    Query(
        description=(
            f"How many rows this page may hold: a whole number from 1 to "
            f"{paging.LIMIT_MAX}, defaulting to {paging.LIMIT_DEFAULT}. Anything else "
            f"is refused."
        )
    ),
]

CursorQuery = Annotated[
    str | None,
    Query(
        description=(
            "Where to carry on from: a fact id this API answered with, as a whole "
            "number. The page holds the facts after it. Absent means the first page."
        )
    ),
]

OwnerCursorQuery = Annotated[
    str | None,
    Query(
        description=(
            "Where to carry on from: the owner name this API answered as the previous "
            "page's `next_cursor`. The page holds the owners strictly after it, which "
            "is what makes the walk total over a set of names. Absent means the first "
            "page."
        )
    ),
]

# A body, exactly as it was sent, handed to a parser of this module's
# own. Not the model as a body type, deliberately and for the reason
# `config/api.py` gives: FastAPI's validation echoes the input it
# rejected, and what these two bodies carry is content.
FactId = Annotated[
    str,
    Path(
        description=(
            "Which fact, by the `id` the facts listing answers with: a whole number, "
            "never reused. Written as a string here because it is parsed by this API "
            "rather than by the framework, whose own refusal for a path segment "
            "describes a request body; what it has to be is a number and nothing "
            "else."
        )
    ),
]

RawBody = Annotated[Any, Body()]

# And the one body that may be absent, which is a different request
# rather than a malformed one: no body clears the whole ledger.
OptionalBody = Annotated[Any, Body()]


def reader(database: DatabaseConfig) -> Callable[[], Iterator[Connection]]:
    """Per-request access to memory: open a connection, yield it,
    dispose the engine.

    The conversation store's own shape, and here for the same reason:
    nothing holds an engine between requests, so a database restored
    underneath a running server is met as it is now. The reply path has
    a long-lived store of its own with two engines; this door
    deliberately borrows neither, because a request is not a session and
    the store's close discipline is written around the calls a reply
    makes.
    """

    def open_reader() -> Iterator[Connection]:
        engine = read_engine(database)
        try:
            with engine.connect() as connection:
                yield connection
        finally:
            engine.dispose()

    return open_reader


@contextlib.contextmanager
def writing(database: DatabaseConfig) -> Iterator[Connection]:
    """One correction's or one deletion's transaction, on a write engine
    opened for it and disposed after it.

    `write_engine` rather than the store's own, because a request
    migrates nothing: boot owns the schema, and a request that ran
    Alembic would be a request that could change the shape of what it
    was asked to correct.

    The memory chain's advisory lock is taken at BEGIN, so this
    transaction and whatever an agent is saying serialize rather than
    interleave: a scope is re-pruned inside this transaction, and the
    count it was pruned against is the count it lands on.
    """
    engine = write_engine(database, MEMORY_CHAIN)
    try:
        with engine.begin() as connection:
            yield connection
    finally:
        engine.dispose()


def _reader(request: Request) -> Iterator[Connection]:
    """Memory, for the length of one request, taken off the
    application's runtime rather than closed over by the routes, so the
    document can be rendered from an application with no database to
    reach."""
    runtime: ApiRuntime = request.app.state.api_runtime
    yield from runtime.memory()


ReaderDep = Annotated[Connection, Depends(_reader)]


def _writer(request: Request) -> Callable[[], AbstractContextManager[Connection]]:
    """How to open one write's transaction.

    The factory rather than the transaction, exactly as the conversation
    namespace's eraser is: a dependency that yielded an open transaction
    would put its commit and its rollback in the framework's hands, and
    what a refusal must not do here is leave half a correction behind.
    Entered inside the handler, the `with` and the arm that classifies
    its failure are the same piece of code.
    """
    runtime: ApiRuntime = request.app.state.api_runtime
    return runtime.memory_writes


WriterDep = Annotated[
    Callable[[], AbstractContextManager[Connection]], Depends(_writer)
]


def routes(api: FastAPI, problems: Callable[..., dict[int | str, dict[str, Any]]]) -> None:
    """The six reads and the four writes, registered on the application
    that is both mounted and rendered.

    The handlers are plain `def`, so FastAPI runs them on the threadpool
    and the synchronous database work never blocks the event loop.

    The agent and device halves are written out twice rather than
    generated from the scope vocabulary, which is what every other route
    family on this API does and for the same reason: a route's name, its
    docstring, its path and what it answers are the committed document's
    bytes, and a factory that set them back onto a generated function
    would put the contract one indirection away from the diff.
    """

    @api.get(
        "/memory/agents",
        response_model=MemoryOwners,
        responses=problems(401, 422, 500, instead=MEMORY_PROBLEMS_INSTEAD),
    )
    def read_agent_owners(
        reader: ReaderDep,
        limit: LimitQuery = None,
        cursor: OwnerCursorQuery = None,
    ) -> dict[str, Any]:
        """Which agents are remembering anything, and how much.

        Every name that has a row, whether or not this deployment still
        has an agent of that name: renaming an agent orphans its memory,
        which is documented rather than prevented, and this listing is
        where an operator sees that it happened.
        """
        return _owners(reader, MemoryScope.AGENT, limit, cursor)

    @api.get(
        "/memory/devices",
        response_model=MemoryOwners,
        responses=problems(401, 422, 500, instead=MEMORY_PROBLEMS_INSTEAD),
    )
    def read_device_owners(
        reader: ReaderDep,
        limit: LimitQuery = None,
        cursor: OwnerCursorQuery = None,
    ) -> dict[str, Any]:
        """Which boards have notes about them, and how many.

        A device's notes are shared by every agent bound to it, so this
        is also the listing that says which places a household's facts
        have accrued in. A board that has been replaced leaves its notes
        behind, and they are answered here.
        """
        return _owners(reader, MemoryScope.DEVICE, limit, cursor)

    @api.get(
        "/memory/conversations",
        response_model=MemoryConversations,
        responses=problems(401, 422, 500, instead=MEMORY_PROBLEMS_INSTEAD),
    )
    def read_conversation_owners(
        reader: ReaderDep,
        limit: LimitQuery = None,
        cursor: OwnerCursorQuery = None,
    ) -> dict[str, Any]:
        """Which conversations hold memory: what each is currently
        keeping, and how much it has forgotten and could bring back.

        A thread's memory shares the thread's lifecycle, so a row here
        for a thread the conversation record no longer has is a row the
        boot sweep has not reached yet. That is the audit point, and it
        is why nothing is filtered out.
        """
        size = paging.limit(limit)
        found = store.conversations_holding_memory(
            reader, after=_owner_cursor(cursor), limit=size + 1
        )
        return paging.page(found, size, key="conversation")

    @api.get(
        "/memory/agents/{name}/facts",
        response_model=MemoryFacts,
        responses=problems(401, 422, 500, instead=MEMORY_PROBLEMS_INSTEAD),
    )
    def read_agent_facts(
        name: str,
        reader: ReaderDep,
        limit: LimitQuery = None,
        cursor: CursorQuery = None,
    ) -> dict[str, Any]:
        """What one agent remembers, oldest first, with the numbers a
        correction and a deletion are addressed by.

        The whole of the scope rather than the part a prompt carries: a
        reply is sent the newest of these inside a byte cap and reaches
        the rest by looking them up, and what an operator is auditing is
        everything that is stored. Facts this agent has forgotten are
        here too, marked by `forgotten_at`.

        An agent with nothing stored answers an empty page rather than a
        refusal, which is also the answer for a name nothing is
        configured under.
        """
        return _facts(reader, MemoryScope.AGENT, name, limit, cursor)

    @api.get(
        "/memory/devices/{mac}/facts",
        response_model=MemoryFacts,
        responses=problems(401, 422, 500, instead=MEMORY_PROBLEMS_INSTEAD),
    )
    def read_device_facts(
        mac: str,
        reader: ReaderDep,
        limit: LimitQuery = None,
        cursor: CursorQuery = None,
    ) -> dict[str, Any]:
        """What is noted about one board and the place it stands in,
        oldest first.

        Shared by every agent bound to that board, which is the whole
        point of the scope and the thing worth auditing about it: a note
        made in one agent's conversation is read into every sibling's
        prompt on that device, and therefore reaches whatever provider
        each of them is configured with.
        """
        return _facts(reader, MemoryScope.DEVICE, _mac(mac), limit, cursor)

    @api.put(
        "/memory/agents/{name}/facts/{id}",
        response_model=MemoryFact,
        responses=problems(401, 404, 409, 422, 500, instead=MEMORY_PROBLEMS_INSTEAD),
        openapi_extra=request_body(MemoryCorrection),
    )
    def correct_agent_fact(
        name: str, id: FactId, body: RawBody, writer: WriterDep
    ) -> dict[str, Any]:
        """Correct what one agent remembers, in place.

        The number does not change and the fact keeps its place in the
        reading order, so a correction is a correction rather than a
        deletion and a fresh write. What changes is the words and the
        moment it was last written.

        The same cap invariant every write to memory is held to: a fact
        whose own line will not fit inside what its scope keeps for all
        of them is refused, because forgetting everything else would not
        make room for it, and a correction that grows a fact re-prunes
        the scope inside this transaction.

        A fact this agent has forgotten is not reachable here. It is
        waiting to be brought back as it was said, and editing it there
        would make that undo answer with something nobody said; erasing
        it below is the door that reaches it.
        """
        return _corrected(writer, MemoryScope.AGENT, name, id, body)

    @api.put(
        "/memory/devices/{mac}/facts/{id}",
        response_model=MemoryFact,
        responses=problems(401, 404, 409, 422, 500, instead=MEMORY_PROBLEMS_INSTEAD),
        openapi_extra=request_body(MemoryCorrection),
    )
    def correct_device_fact(
        mac: str, id: FactId, body: RawBody, writer: WriterDep
    ) -> dict[str, Any]:
        """Correct one of a board's notes, in place, under the same
        rules the agent half is corrected under."""
        return _corrected(writer, MemoryScope.DEVICE, _mac(mac), id, body)

    @api.delete(
        "/memory/agents/{name}/facts/{id}",
        response_model=MemoryErasure,
        responses=problems(401, 404, 409, 422, 500, instead=MEMORY_PROBLEMS_INSTEAD),
    )
    def erase_agent_fact(name: str, id: FactId, writer: WriterDep) -> dict[str, int]:
        """Erase one thing an agent remembers.

        A hard delete, held facts included, and nothing here keeps it
        for an undo: the soft forgetting an agent does belongs to the
        conversation that spoke it, and this door is correction and
        audit rather than that flow. What is removed is gone at the next
        reply's prompt.
        """
        return _erased(writer, MemoryScope.AGENT, name, id)

    @api.delete(
        "/memory/devices/{mac}/facts/{id}",
        response_model=MemoryErasure,
        responses=problems(401, 404, 409, 422, 500, instead=MEMORY_PROBLEMS_INSTEAD),
    )
    def erase_device_fact(mac: str, id: FactId, writer: WriterDep) -> dict[str, int]:
        """Erase one of a board's notes, under the same rules."""
        return _erased(writer, MemoryScope.DEVICE, _mac(mac), id)

    @api.delete(
        "/memory/agents/{name}/facts",
        response_model=MemoryErasure,
        responses=problems(401, 409, 422, 500, instead=MEMORY_PROBLEMS_INSTEAD),
    )
    def erase_agent_memory(name: str, writer: WriterDep) -> dict[str, int]:
        """Erase everything one agent remembers, in one transaction.

        Addressed at an owner rather than at a row, so an agent with
        nothing stored is erased of nothing and the count says so. This
        is the verb for an orphan the listings turned up: a renamed
        agent's rows have no other way out.
        """
        return _cleared(writer, MemoryScope.AGENT, name)

    @api.delete(
        "/memory/devices/{mac}/facts",
        response_model=MemoryErasure,
        responses=problems(401, 409, 422, 500, instead=MEMORY_PROBLEMS_INSTEAD),
    )
    def erase_device_memory(mac: str, writer: WriterDep) -> dict[str, int]:
        """Erase every note about one board, in one transaction, which
        is what a board leaving a household needs."""
        return _cleared(writer, MemoryScope.DEVICE, _mac(mac))

    @api.get(
        "/memory/conversations/{conversation}/state",
        response_model=MemoryState,
        responses=problems(401, 422, 500, instead=MEMORY_PROBLEMS_INSTEAD),
    )
    def read_conversation_state(conversation: str, reader: ReaderDep) -> dict[str, Any]:
        """What one conversation is currently keeping, by key.

        The ledger the agent writes as it goes: the position on a board,
        the scene it is in, what the lesson is up to. It is answered
        whole rather than a page at a time, because a write past the
        ledger's own caps is refused, so the whole of one is bounded.

        A thread that is keeping nothing answers an empty list, which is
        also the answer for a thread that never existed.
        """
        return {"items": store.ledger_of(reader, conversation)}

    @api.delete(
        "/memory/conversations/{conversation}/state",
        response_model=MemoryStateErasure,
        responses=problems(401, 404, 409, 422, 500, instead=MEMORY_PROBLEMS_INSTEAD),
        openapi_extra=request_body(MemoryStateKey, required=False),
    )
    def clear_conversation_state(
        conversation: str, writer: WriterDep, body: OptionalBody = None
    ) -> dict[str, int]:
        """Clear one entry of a conversation's ledger, or the whole of
        it.

        The entry is named in the body and never in the path: a key is a
        word the model chose, so it can be credential-shaped, and a path
        reaches proxy and access logs where a body does not. A request
        with no body at all clears the ledger, which is a deliberate
        difference rather than a default: a request that lost its body
        on the way would otherwise erase everything.

        A named entry that is not there is a 404. The whole-ledger form
        is not addressed at an entry, so it takes what is there and the
        count says how much that was.

        The deletion runs inside the order a thread's erasure publishes
        through, so it and a transaction erasing the same thread are
        ordered rather than interleaved and this count is a count of
        rows this request took.
        """
        key = _key(body)
        with erasure_order():
            taken = _written(
                writer, lambda connection: _cleared_state(connection, conversation, key)
            )
        return {"state": taken}


def _owners(
    reader: Connection, scope: MemoryScope, limit: str | None, cursor: str | None
) -> dict[str, Any]:
    """One page of the owners in one scope, whichever scope asked."""
    size = paging.limit(limit)
    found = store.owners(reader, scope, after=_owner_cursor(cursor), limit=size + 1)
    return paging.page(found, size, key="owner")


def _facts(
    reader: Connection,
    scope: MemoryScope,
    owner: str,
    limit: str | None,
    cursor: str | None,
) -> dict[str, Any]:
    """One page of one owner's facts, whichever scope asked."""
    size = paging.limit(limit)
    found = store.facts_of(
        reader, scope, owner, after=paging.cursor(cursor), limit=size + 1
    )
    return paging.page(found, size)


def _corrected(
    writer: Callable[[], AbstractContextManager[Connection]],
    scope: MemoryScope,
    owner: str,
    fact_id: str,
    body: object,
) -> dict[str, Any]:
    """One correction, whichever scope asked, with the body read before
    a transaction is opened: a body this endpoint cannot read is the
    caller's mistake and needs no connection to answer."""
    text = _fact(body)
    number = _fact_id(fact_id)

    def correcting(connection: Connection) -> dict[str, Any]:
        found = store.correct(connection, scope, owner, number, text)
        if found is None:
            raise UnknownEntityError(_UNKNOWN_FACT)
        return dict(found)

    return _written(writer, correcting)


def _erased(
    writer: Callable[[], AbstractContextManager[Connection]],
    scope: MemoryScope,
    owner: str,
    fact_id: str,
) -> dict[str, int]:
    """One addressed deletion, whichever scope asked. Addressed and not
    there is a 404 rather than an erasure of nothing: a caller that
    named one fact meant that fact."""

    number = _fact_id(fact_id)

    def erasing(connection: Connection) -> int:
        taken = store.erase_fact(connection, scope, owner, number)
        if not taken:
            raise UnknownEntityError(_UNKNOWN_FACT)
        return taken

    return {"facts": _written(writer, erasing)}


def _cleared(
    writer: Callable[[], AbstractContextManager[Connection]],
    scope: MemoryScope,
    owner: str,
) -> dict[str, int]:
    """One whole scope emptied, whichever scope asked."""
    return {
        "facts": _written(
            writer, lambda connection: store.erase_facts(connection, scope, owner)
        )
    }


def _cleared_state(connection: Connection, conversation: str, key: str | None) -> int:
    taken = store.clear_ledger(connection, conversation, key)
    if key is not None and not taken:
        raise UnknownEntityError(_UNKNOWN_ENTRY)
    return taken


def _written[T](
    writer: Callable[[], AbstractContextManager[Connection]],
    work: Callable[[Connection], T],
) -> T:
    """One write's transaction, whatever it writes.

    The failure arm is the whole no-leak surface of a write and it is
    here once for all four of them. A driver exception carries the
    statement it failed on and the values bound into it, which here are
    an owner's name, a remembered fact and a model-chosen key, so which
    sentence is answered is decided by the db classifier's closed set and
    the exception itself is dropped: built inside the arm, raised outside
    it, so nothing walking the chain finds it either.

    A refusal this module or the store decided travels unchanged, which
    is what lets a 404 and an over-cap correction leave through the same
    boundary as the two storage sentences. Its transaction is rolled back
    on the way out, so a refused write changed nothing.
    """
    problem: ConfigError | None = None
    try:
        with writer() as connection:
            return work(connection)
    except ConfigError:
        raise
    except Exception as exc:  # noqa: BLE001 - the driver's own words never travel
        problem = DatabaseBusyError(_MEMORY_BUSY) if is_busy(exc) else StorageError(
            _MEMORY_FAILED
        )
    raise problem


def _fact_id(value: str) -> int:
    """The number in the path, or the refusal naming what it has to be.

    Bounded by the range of the identity column as well as by its
    spelling, so a number no row can carry is the caller's mistake here
    rather than a driver error and a storage failure further down.
    """
    number = paging.whole(value)
    if number is None or number > paging.MAX_ROW_ID:
        raise ConfigError(_NOT_A_FACT_NUMBER)
    return number


def _owner_cursor(value: str | None) -> str | None:
    """Where an owner listing carries on from, or None for the first
    page.

    Bounded before it is used rather than parsed: an owner is a name and
    every name is one this deployment or a board minted, so the only
    rule there is to enforce is that nothing unbounded is bound into a
    statement.
    """
    if value is None:
        return None
    if not value or len(value) > _OWNER_LENGTH:
        raise ConfigError(_OWNER_CURSOR_REFUSED)
    return value


def _mac(value: str) -> str:
    """The board in the path, normalized the way every other MAC in this
    project is, so `AA-BB-...` and `aa:bb:...` reach the same notes.

    Refused rather than matched literally when it is not a MAC: an empty
    page would be a true answer to a question the caller did not mean to
    ask, and the owner a note is stored under is canonical by
    construction.
    """
    problem: ConfigError | None = None
    try:
        return normalize_mac(value)
    except ValueError:
        # Built here and raised outside the arm, the rule this codebase
        # settled on: a refusal raised inside the arm carries whatever
        # the caught exception held as its `__context__`.
        problem = ConfigError(_NOT_A_MAC)
    raise problem


def _fact(body: object) -> str:
    """The corrected text out of a body, or the sentence saying what the
    body has to be.

    Plain checks and no try/except, the rule `config/api.py`'s own body
    parsers keep: a refusal raised inside a handler carries the
    exception being handled as its context, and a KeyError or a
    TypeError raised on a body holds the body.
    """
    return _sole(body, "fact", _CORRECTION_BODY)


def _key(body: object) -> str | None:
    """The entry named in a body, or None where there was no body, which
    is the request that clears the whole ledger."""
    if body is None:
        return None
    return _sole(body, "key", _STATE_BODY)


def _sole(body: object, key: str, expectation: str) -> str:
    if not isinstance(body, dict) or set(body) != {key}:
        raise ConfigError(expectation)
    value = body[key]
    if not isinstance(value, str) or not value:
        raise ConfigError(expectation)
    return value


__all__ = [
    "MEMORY_PROBLEMS_INSTEAD",
    "MemoryConversation",
    "MemoryConversations",
    "MemoryCorrection",
    "MemoryEntry",
    "MemoryErasure",
    "MemoryFact",
    "MemoryFacts",
    "MemoryOwner",
    "MemoryOwners",
    "MemoryState",
    "MemoryStateErasure",
    "MemoryStateKey",
    "reader",
    "routes",
    "writing",
]
