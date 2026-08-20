"""Applying a freshly read configuration to what is running.

Two phases and one exclusion. Preparation re-reads the stored
configuration and builds every manager the new world needs with
nothing running touched, so any failure there is a refusal that
changed nothing; application then stops what is going, starts what
is new, and swaps the slice, in one registry method with no await
in it.

The exception types a refusal raises ARE the contract with the API:
`ReloadInProgressError` and `DatabaseBusyError` are the 409s, a
`ConfigError` is the 422, and a `StorageError` is the 500. They are
the configuration layer's own types, raised here unwrapped and
carrying sentences this application composed, which is why the
handler above can put one straight into a response body.

The functions take the registry they change rather than sitting on
it: what a reload is about is the two phases and the order of them,
and that is a story of its own beside the registry's ordinary
reads.

The answer the API sends is composed here too, for the same reason:
what one reload did and what is running once it had done it are one
answer taken in one breath, and which of the two the caller is
allowed to read a moment later is knowledge this file has and a
request handler should not have to hold.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vinga_server.config import Config
from vinga_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    ReloadInProgressError,
    StorageError,
)
from vinga_server.config.responses import McpReloadResult
from vinga_server.config.secrets import SecretStore
from vinga_server.events.catalog import McpReloadApplied, McpReloadRefused
from vinga_server.events.values import Count, McpRefusalToken, Whole

from . import events
from .manager import McpConfigError, McpManager, McpServerManager, _managers_for, _stopped
from .slice import McpSlice

if TYPE_CHECKING:
    from .registry import McpServers


@dataclass(frozen=True)
class McpReload:
    """What one reload did to the running servers, by entry name.

    Four outcomes and no fifth: every configured entry the new world
    references is one of the first three or unchanged, and an entry that
    went away is stopped. What is deliberately not here is whether a
    server that was started came up: that is the status surface's
    answer, taken in the same breath by whoever asked for the reload,
    because a start that connected to nothing is a reload that applied.
    """

    started: tuple[str, ...] = ()
    restarted: tuple[str, ...] = ()
    stopped: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()


# What a reload refused during preparation says, in front of the
# refusal's own sentence. The lead is the operationally important half:
# a caller has to know that the servers are as they were, not half way
# to something else.
RELOAD_REFUSED = "the reload was refused and nothing was changed:"

RELOAD_IN_PROGRESS = (
    "a reload of this server's MCP servers is already running. Nothing was changed by "
    "this request; make it again once the first has answered."
)

# And what it says when the re-read failed in a way the configuration
# layer has no type for, which is the one refusal whose exception is not
# already this application's own words. A `StorageError`, because that
# is exactly what this is (stored state that could not be read, through
# no fault of the caller) and because its type is what the API turns
# into a status. The failure itself is named by class in the event
# beside this, which is where an operator looks; a `read` callable that
# fails unexpectedly may be holding a connection string, and the reload
# endpoint's response body is not the place to find out.
RELOAD_UNREADABLE = (
    f"{RELOAD_REFUSED} this server's configuration could not be read. The failure is "
    "recorded in this server's log."
)

# What one reload did, as the `mcp_reload` event says it. Two outcomes
# and no third, because the two phases leave no other end: preparation
# refuses with nothing touched, or the apply runs to its end. An apply
# that started a server which then failed to connect is `applied`, which
# is the reload's own rule (`McpReload` deliberately counts no failures)
# and the server says so itself, in an `mcp_down` of its own.
APPLIED = "applied"
REFUSED = "refused"

# And why a refused one was refused: a closed set taken from the
# refusals that actually exist, one token per type, chosen where the
# exception is classified and never built out of its message. The
# messages themselves still travel to the caller that asked, which is
# where a sentence naming a configuration location belongs; what goes
# into a field a collector groups by is the kind of refusal it was.
REFUSED_IN_PROGRESS = "in_progress"
REFUSED_BUSY = "database_busy"
REFUSED_UNREADABLE = "unreadable"
REFUSED_INVALID = "invalid"
# The net under the four, for a `read` callable that fails in a way the
# configuration layer has no type for. Nothing in the tree raises it
# today, and a closed set with no fallback would be a set that quietly
# opens the first time something does.
REFUSED_UNEXPECTED = "unexpected"


def _refusal(exc: BaseException) -> str:
    """Which token one refused reload's exception is.

    Ordered from the most specific down, because the three named types
    are all `ConfigError`s: their type is the answer the API turns into
    a status code, and it is the same answer this event carries.
    """
    if isinstance(exc, ReloadInProgressError):
        return REFUSED_IN_PROGRESS
    if isinstance(exc, DatabaseBusyError):
        return REFUSED_BUSY
    if isinstance(exc, StorageError):
        return REFUSED_UNREADABLE
    if isinstance(exc, ConfigError):
        return REFUSED_INVALID
    return REFUSED_UNEXPECTED


# What a reload's first phase produces: the world it read, and the
# managers that world needs, neither of them installed anywhere yet.
# Named because it is now a task's result type as well as a return
# value, and a task is declared where its result is not in sight.
type _Preparation = tuple[McpSlice, dict[str, McpServerManager]]


async def reload(
    servers: "McpServers", read: Callable[[], tuple[Config, SecretStore | None]]
) -> McpReload:
    """Apply a freshly read configuration to what is running.

    `read` is the re-read of the stored configuration, handed in
    rather than done here: opening a database belongs to the layer
    that owns one, and this layer owns where it runs. It runs in a
    worker thread, because it takes the database's write lock and
    waits out its busy timeout, and this coroutine is on the event
    loop that every live conversation is on.

    Two phases, and only the second touches anything running.
    Preparation validates and builds every manager the new world
    needs; any failure there (an unset `$VAR`, a credential that will
    not decrypt, an egress declaration `server.local_only` forbids)
    refuses with the managers and the grants exactly as they were.
    Application then stops what is going, starts what is new, and
    swaps the slice, so the grants change at one instant rather than
    across one.

    Being unreachable is not a preparation failure, which is the
    boot's rule carried over: a candidate that connects to nothing
    applies as a down manager with its reason on the status surface,
    revived when a session that would use it opens.

    One at a time. A second reload while one is running is refused
    rather than queued, because it would carry a configuration read
    later than the first one's into a world the first one is halfway
    through changing.

    The second phase finishes whatever happens to the caller. A
    client that disconnects cancels the handler awaiting this, and a
    cancellation landing between the stops and the swap would leave
    stopped managers in the live set and started ones reachable by
    nobody, with the exclusion released as though the reload were
    done. So the apply runs in a task of its own behind a shield:
    cancelling the request cancels the waiting, and the world still
    arrives in one piece.

    Which is also why the `mcp_reload` event is emitted at the two
    ends rather than here: a refusal says so where it is
    classified, and an apply says so as its last act, from inside
    the shielded task. One reload is therefore one event, whether
    or not anybody is still waiting for the answer.

    The preparation is behind a shield of its own, and for a
    different reason. Nothing it does can leave a half-changed
    world, but its re-read runs in a worker thread, and a thread
    cannot be cancelled: a client that disconnects during it leaves
    it holding the database's write lock for as long as it takes.
    Releasing the exclusion there would let the next reload start a
    read against a lock the last one still holds, and answer a
    caller who did nothing wrong that the database is busy. So both
    halves are owned tasks, and the exclusion is held until
    whichever of them is still running has finished.
    """
    if servers._reloading:
        _refused(REFUSED_IN_PROGRESS)
        raise ReloadInProgressError(RELOAD_IN_PROGRESS)
    servers._reloading = True
    # When the request was accepted, which is what the applied
    # event's duration is measured from: see `_apply`.
    began = time.monotonic()
    preparing: asyncio.Task[_Preparation] | None = None
    applying: asyncio.Task[McpReload] | None = None
    try:
        preparing = asyncio.create_task(
            _preparation(servers, read), name="mcp-reload-read"
        )
        servers._preparing = preparing
        configured, candidates = await asyncio.shield(preparing)
        applying = asyncio.create_task(
            _apply(servers, configured, candidates, began), name="mcp-reload"
        )
        servers._applying = applying
        return await asyncio.shield(applying)
    finally:
        # The apply exists only once the preparation returned, so
        # the later of the two is the one still capable of running,
        # and it is the one the exclusion waits on.
        servers._hold_until(applying if applying is not None else preparing)


async def reload_result(
    servers: "McpServers", read: Callable[[], tuple[Config, SecretStore | None]]
) -> McpReloadResult:
    """One reload, as the answer the API sends: what it did, and what
    every configured entry is doing now that it has been done.

    Both halves in one reply is the endpoint's contract, and taking
    them together is this file's job rather than a handler's. The
    outcomes are `McpReload`'s four tuples, which are this
    application's own vocabulary; the status is the same document
    `GET /runtime/mcp-servers` answers with, so applying and verifying
    are one round trip.

    Read with no await between the reload returning and the status
    being taken, which is the invariant that makes the two halves one
    world: a reload landing in between would leave outcomes describing
    a world the status no longer reports.

    Refusals are not caught. The exception types the two phases raise
    ARE the contract with the API (409, 422, 500), so they travel out
    of here exactly as they left `_preparation`.
    """
    applied = await reload(servers, read)
    return McpReloadResult(
        started=list(applied.started),
        restarted=list(applied.restarted),
        stopped=list(applied.stopped),
        unchanged=list(applied.unchanged),
        servers=servers.typed_status(),
    )


async def _preparation(
    servers: "McpServers", read: Callable[[], tuple[Config, SecretStore | None]]
) -> _Preparation:
    """The first phase, whole: the re-read, the slice it composes,
    and every manager the new world needs, with nothing running
    touched.

    Gathered into one call so that the refusal has one place to be
    said out loud. Both halves refuse the same way as far as a
    caller is concerned (nothing changed, and here is why), and an
    operator counting refused reloads does not care which half of a
    preparation the refusal came out of, which is the same thing
    `_read`'s own wording already decided.

    A cancellation is not a refusal and is not reported as one: the
    caller went away before the world was even a candidate, and
    nothing happened that anybody has to be told about.

    The two failure branches differ in one thing only: whether the
    exception is already this application's own words. A
    `ConfigError` is, by construction, since `_read` and `_prepared`
    both compose theirs outside their own handlers and their
    messages name configuration locations rather than values, and
    the API puts that message straight into a response body.
    Anything else came from the `read` callable, whose failures
    nothing bounds, so it is classified here and answered with a
    fixed sentence built after the handler has closed: the token is
    the diagnosis, and what a database driver had to say about a
    connection string is not part of the reload's answer.
    """
    problem: str | None = None
    try:
        config, secrets = await _read(read)
        # One slice, composed before preparation and applied after
        # it, so the world the candidates were built for is the
        # world that gets installed.
        configured = McpSlice.of(config)
        return configured, _prepared(config, secrets, configured)
    except asyncio.CancelledError:
        raise
    except ConfigError as exc:
        _refused(_refusal(exc))
        raise
    except Exception as exc:
        _refused(_refusal(exc))
        problem = RELOAD_UNREADABLE
    raise StorageError(problem)


def _refused(reason: str) -> None:
    """One reload that changed nothing, said once.

    The token and nothing else. The refusal's own sentence names a
    configuration location and travels to whoever asked for the
    reload, which is where it belongs; this line is the one an
    operator finds afterwards, and what it has to answer is that
    the servers are as they were.
    """
    events.emit(lambda: McpReloadRefused(reason=McpRefusalToken(reason)))


async def _read(
    read: Callable[[], tuple[Config, SecretStore | None]],
) -> tuple[Config, SecretStore | None]:
    """The stored configuration, read off this loop, refusing in the
    words the other half of the preparation refuses in.

    In a worker thread because it takes the database's write lock
    and waits out its busy timeout, and this coroutine is on the
    loop every live conversation is on.

    A stored snapshot that will not compose is as much a reload that
    changed nothing as a candidate that would not build, and an
    operator reading the two sentences should not have to work out
    which half of the preparation they are in. The two exceptions
    keep their own type because their type is the answer: a busy
    database is retryable and answers 409, unreadable stored state
    is not the caller's fault and answers 500.

    Recorded and re-raised outside the handler, the rule this
    codebase settled on: raised inside one, the refusal would carry
    whatever the read was holding when it failed.
    """
    problem: str | None = None
    try:
        return await asyncio.to_thread(read)
    except (DatabaseBusyError, StorageError):
        raise
    except ConfigError as exc:
        problem = f"{RELOAD_REFUSED} {exc}"
    raise ConfigError(problem)


def _prepared(
    config: Config, secrets: SecretStore | None, configured: McpSlice
) -> dict[str, McpServerManager]:
    """Every manager the new configuration needs, built while
    nothing running has been touched.

    The refusal is recorded and raised outside the handler, the rule
    this codebase settled on: raised inside one, it would carry the
    exception being handled as its context, and one of these is a
    decryption failure. Its message names locations and never
    values, which is what lets it travel out as the API's sanitized
    sentence.
    """
    problem: str | None = None
    try:
        return _managers_for(config, secrets, configured)
    except (McpConfigError, ConfigError) as exc:
        problem = f"{RELOAD_REFUSED} {exc}"
    raise ConfigError(problem)


async def _apply(
    servers: "McpServers",
    configured: McpSlice,
    candidates: dict[str, McpServerManager],
    began: float,
) -> McpReload:
    """The second phase: the diff, the lifecycles, and the swap.

    The lifecycle work is concurrent the way `start_all` already
    connects at boot, so the whole of it is one connect timeout plus
    small change rather than a sum over servers. Stops first and
    starts after them, so an entry whose command was edited does not
    have two copies of the same child process alive at once.

    `began` is when the request was accepted, not when this phase
    started, and it is passed in for that reason. The reported
    duration is what the caller waited: an operator reading "the
    reload took nine seconds" is asking about the request they made,
    and a reload spends its time in whichever half is slow. The
    re-read waits out a busy database's timeout and the starts wait
    out a connect timeout, and a number that covered only the second
    would call a reload fast on exactly the days it was not.
    """
    keep: dict[str, McpManager] = {}
    started: list[str] = []
    restarted: list[str] = []
    unchanged: list[str] = []
    going: list[McpManager] = []
    arriving: list[McpManager] = []
    for name, candidate in candidates.items():
        running = servers._managers.get(name)
        if running is not None and running.same_as(candidate):
            # The connection stands; only what the grants name of it
            # may have moved, which the kept manager is told so a
            # newly allowed name that never published is still said
            # out loud.
            running.expect(configured.allowed_names(name))
            keep[name] = running
            unchanged.append(name)
            continue
        if running is not None:
            going.append(running)
            restarted.append(name)
        else:
            started.append(name)
        keep[name] = candidate
        arriving.append(candidate)
    stopped = sorted(set(servers._managers) - set(candidates))
    going += [servers._managers[name] for name in stopped]

    if going:
        await asyncio.gather(*(_stopped(manager) for manager in going))
    if arriving:
        await asyncio.gather(*(manager.start() for manager in arriving))
    servers._install(keep, configured)
    # Counts rather than names, in the fields as in the sentence.
    # Entry names are the operator's own words and would be safe to
    # print, but what this event answers is how much a reload moved
    # and how long it took; which entries they were is the status
    # surface's answer, taken in the same breath by whoever asked.
    events.emit(
        lambda: McpReloadApplied(
            started=Count(len(started)),
            restarted=Count(len(restarted)),
            stopped=Count(len(stopped)),
            unchanged=Count(len(unchanged)),
            duration_ms=Whole(round((time.monotonic() - began) * 1000)),
        )
    )
    return McpReload(
        started=tuple(started),
        restarted=tuple(restarted),
        stopped=tuple(stopped),
        unchanged=tuple(unchanged),
    )
