"""Applying a freshly read configuration to the MCP servers running.

Two phases. Preparation composes the candidate slice and builds
every manager the new world needs with nothing running touched, so
any failure there is a refusal that changed nothing; application
then stops what is going, starts what is new, and swaps the slice,
in one registry method with no await in it.

Both phases are steps of a larger apply rather than an action of
their own (#191): the generalized reload in `config/reload.py` owns
the re-read, the exclusion that lets one apply run at a time, the
shields that finish a phase whose caller went away, and the order
the halves of a world are put in place in. What is left here is what
only this package can do, which is knowing what an MCP world is made
of and what it costs to replace one.

The exception types a refusal raises ARE the contract with the API:
`DatabaseBusyError` is a 409, a `ConfigError` is the 422, and a
`StorageError` is the 500. They are the configuration layer's own
types, raised here unwrapped and carrying sentences this application
composed.

The functions take the registry they change rather than sitting on
it: what a reload is about is the two phases and the order of them,
and that is a story of its own beside the registry's ordinary
reads.

The MCP half of the answer the API sends is composed here too, for
the same reason: what one reload did to these servers and what they
are doing once it had done it are one answer taken in one breath,
and which of the two the caller is allowed to read a moment later is
knowledge this file has and a request handler should not have to
hold.
"""

import asyncio
import time
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
from vinga_server.events.values import Count, McpRefusal, Whole

from . import events
from .manager import McpConfigError, McpManager, McpServerManager, _managers_for, _stopped
from .slice import McpSlice

if TYPE_CHECKING:
    from .registry import McpServers


# What a reload refused during preparation says, in front of the
# refusal's own sentence. The lead is the operationally important half:
# a caller has to know that the servers are as they were, not half way
# to something else.
RELOAD_REFUSED = "the reload was refused and nothing was changed:"

# What one reload did, as the `mcp_reload` event says it. Two outcomes
# and no third, because the two phases leave no other end: preparation
# refuses with nothing touched, or the apply runs to its end. An apply
# that started a server which then failed to connect is `applied`, which
# is the reload's own rule (the outcomes deliberately count no failures)
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
# Named because it is a task's result type as well as a return value,
# and a task is declared where its result is not in sight.
type McpCandidate = tuple[McpSlice, dict[str, McpServerManager]]


async def prepare(config: Config, secrets: SecretStore | None) -> McpCandidate:
    """The first phase, whole: the slice the freshly read configuration
    composes, and every manager the new world needs, with nothing
    running touched.

    `config` and `secrets` are the stored half as the generalized
    reload read it, handed in rather than read here: opening a database
    belongs to the layer that owns one, and this layer owns what an MCP
    world costs to build.

    A refusal raised here is already this application's own words by
    construction, since `_prepared` composes its message outside its own
    handler and it names configuration locations rather than values. It
    is not said out loud here: one reload is one event, and the phase
    that owns the whole apply is the one place every refusal passes
    through.
    """
    # One slice, composed before preparation and applied after it, so
    # the world the candidates were built for is the world that gets
    # installed. The secrets go in with it: the slice carries the
    # comparison identity of every configured entry, and what is stored
    # behind an entry is half of one.
    configured = McpSlice.of(config, secrets)
    return configured, _prepared(config, secrets, configured)


def refused(exc: BaseException) -> None:
    """One reload that changed nothing, said once, by kind.

    Public because the generalized reload refuses in three places this
    package never sees (the stored re-read, the composition of the
    candidate generation, and its whole-snapshot validation) and one
    reload is one event whichever of them it ends at. The event's name
    and fields are a committed surface, so it stays the `mcp_reload` one
    it has always been; widening what it is called is a change to the
    event reference and not to this milestone.

    The token and nothing else. The refusal's own sentence names a
    configuration location and travels to whoever asked for the reload,
    which is where it belongs; this line is the one an operator finds
    afterwards, and what it has to answer is that the servers are as
    they were.
    """
    # `_refusal` answers one of this module's own `REFUSED_*`
    # constants, which are package surface a caller reads; the lookup
    # that crosses them into the event vocabulary belongs here rather
    # than in their definitions.
    events.emit(lambda: McpReloadRefused(reason=McpRefusal(_refusal(exc))))


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


async def apply(
    servers: "McpServers", candidate: McpCandidate, began: float
) -> McpReloadResult:
    """The second phase: the diff, the lifecycles, the swap, and the
    answer taken beside it.

    What it did and what every configured entry is doing now that it
    has been done, in one value: both halves in one reply is the
    endpoint's contract, and taking them together is this file's job
    rather than a handler's. The status is read with no await between
    the swap and the reading, which is the invariant that makes the two
    halves one world; a second apply landing in between would leave
    outcomes describing a world the status no longer reports, and the
    exclusion the generalized reload holds is what makes that
    impossible rather than merely unlikely.

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
    configured, candidates = candidate
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
    return McpReloadResult(
        started=started,
        restarted=restarted,
        stopped=stopped,
        unchanged=unchanged,
        servers=servers.typed_status(),
    )
