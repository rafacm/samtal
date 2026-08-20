"""Applying the stored configuration to a running server.

One verb, and it is the configuration's rather than any one kind's
(#191). The MCP reload proved the shape: a prepare phase that can only
refuse, then a swap of what new work binds to. What is here is that
shape generalized, so that a caller asks for the stored configuration to
be applied and never for one half of it, and the halves that converge at
different moments say so in the answer rather than in the caller's head.

Two phases and one exclusion.

Preparation re-reads the stored domain half, composes the world this
server may actually serve, validates it whole, synthesizes the filled
pauses that world needs and builds every MCP manager it needs, with
nothing running touched: a failure anywhere in it is a refusal that
changed nothing. Application then puts the world in place, generation
first and MCP install after it, inside one instability window, so a
reader either sees the world before or the world after and never a
mixture.

The one thing preparation does that cannot refuse is the synthesis. A
filled pause is a latency mask, so an agent whose voice will not speak
applies with no clip and runs with the mask off, exactly as a boot
leaves it; a posture where a text-to-speech hiccup blocked a prompt fix
would invert which of the two matters.

The world a generation serves is an OVERLAY and never the stored
snapshot whole, which is the part worth reading twice. Most of the
domain half is still restart-bound: the agent set, `agent_defaults`, the
providers. The effective-value helpers inherit through exactly those, so
installing the stored snapshot would apply restart-bound changes by
inheritance and let an activation index an agent the store has deleted.
So the candidate generation is the previous one with only the slices
this server can really apply replaced from the store, and the whole of
it is validated again: an overlay that no longer composes (a fragment
deleted from the store while a restart-bound `prompt_includes` still
names it) is an apply that has to wait for the restart, and it is
refused in those words rather than half applied. The overlay narrows as
the milestones widen and retires when there is nothing restart-bound
left in the domain half.

The exception types a refusal raises ARE the contract with the API:
`ReloadInProgressError` and `DatabaseBusyError` are the 409s, a
`ConfigError` is the 422, and a `StorageError` is the 500. The sentences
they carry name configuration locations, and the composition root
replaces the ones composed over stored state with fixed ones before they
reach a response body; the type is what survives, because the type is
what decides the status.

Deletion test: inlined into `api.py` the route would learn what a reload
is made of, which is the one thing that module refuses to know; inlined
into `app.py` the composition root would grow a second responsibility
beside wiring.
"""

import asyncio
import contextlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from vinga_server.config.diff import OVERLAID_AGENT_FIELDS, Loaded
from vinga_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    ReloadInProgressError,
    StorageError,
)
from vinga_server.config.models import (
    Config,
    check_completeness,
    check_references,
)
from vinga_server.config.responses import (
    ConfigReloadResult,
    FillersReload,
    PromptsReload,
)
from vinga_server.config.secrets import EntityKind
from vinga_server.filler import Fillers, build_agent_fillers
from vinga_server.generation import Generation, Generations
from vinga_server.providers import AgentProviders
from vinga_server.tools.mcp import McpServers
from vinga_server.tools.mcp import reload as mcp

# Which entity kinds' stored credentials this server applies rather than
# carries over. An MCP server's are read as its connection is made, and
# a reload makes it again; a provider's are read as the provider is
# built, which is still the boot, so a rotation there stays pending in
# the diff until the milestone that rebuilds providers. Declared as data
# beside the overlay it governs, and widened one member at a time.
LIVE_SECRETS: frozenset[EntityKind] = frozenset({"mcp_server"})

RELOAD_IN_PROGRESS = (
    "a reload of this server's configuration is already running. Nothing was changed by "
    "this request; make it again once the first has answered."
)

# What a reload says when the re-read failed in a way the configuration
# layer has no type for, which is the one refusal whose exception is not
# already this application's own words. A `StorageError`, because that
# is exactly what this is (stored state that could not be read, through
# no fault of the caller) and because its type is what the API turns
# into a status. The failure itself is named by class in the event
# beside this, which is where an operator looks; a `read` callable that
# fails unexpectedly may be holding a connection string, and the reload
# endpoint's response body is not the place to find out.
RELOAD_UNREADABLE = (
    f"{mcp.RELOAD_REFUSED} this server's configuration could not be read. The failure "
    "is recorded in this server's log."
)


@dataclass(frozen=True)
class _Candidate:
    """A whole new world, built and refusable, installed nowhere.

    Everything the apply phase needs, gathered by the prepare phase so
    that the apply is assignment and lifecycle work and no decisions:
    what a caller waited for is decided while nothing running has been
    touched.
    """

    generation: Generation
    prompts: tuple[str, ...]
    fillers: Fillers
    mcp: mcp.McpCandidate


class ConfigReload:
    """One running server's apply of what is stored, and the exclusion
    that lets one of them run at a time.

    A class rather than a closure because the exclusion is state that
    outlives a request and has to be readable from outside: something
    waiting for the world to settle asks whether an apply is still
    running, and a caller that went away does not make one stop.

    The composition root builds one of these and hands `apply` to the
    API as the reload it may call, which is why the two phases below are
    private: what a caller may do is ask for the stored configuration to
    be applied, and the order the halves go in is not theirs to choose.
    """

    def __init__(
        self,
        generations: Generations,
        servers: McpServers,
        read: Callable[[], Loaded],
        agent_providers: Mapping[str, AgentProviders],
    ) -> None:
        self._generations = generations
        self._servers = servers
        # The engines this server is running, which the filler synthesis
        # needs one of: the voice a clip is spoken in is the one that is
        # actually running, because providers are built at a start and a
        # reload does not replace them yet (#191). Handed in for the
        # reason the read is, and required rather than optional because
        # every server has them; the milestone that makes providers
        # generational is where this stops being a field.
        self._agent_providers = agent_providers
        # The stored half, handed in rather than read here: opening a
        # database belongs to the layer that owns one, and what this
        # class owns is where that read runs and what is done with what
        # comes back.
        self._read = read
        # Whether an apply is between its two phases right now. A plain
        # flag rather than a lock because a second apply is refused
        # rather than queued: it would carry a configuration read after
        # the first one's into a world the first one is in the middle of
        # replacing.
        self._running = False
        # The apply in flight, if any. Held because it outlives the
        # request that asked for it when that request is cancelled, and
        # the loop keeps only a weak reference to a task nobody awaits.
        self._applying: asyncio.Task[ConfigReloadResult] | None = None
        # And the preparation in flight, held for that reason and for a
        # stronger one. Its re-read runs in a worker thread, taking the
        # database's write lock and waiting out its busy timeout, and a
        # thread is not a thing that can be cancelled: a caller who goes
        # away while it is running leaves it running. The exclusion has
        # to outlive it or the next apply's read meets the first one's
        # still holding the lock, and answers a caller who did nothing
        # wrong that the database is busy.
        self._preparing: asyncio.Task[_Candidate] | None = None

    @property
    def running(self) -> bool:
        """Whether an apply is between its two phases right now.

        The exclusion, read from outside. An apply outlives the caller
        that asked for it, so this is how anything waiting for the world
        to settle knows that the second phase has finished and the next
        apply would be answered rather than refused.
        """
        return self._running

    async def apply(self) -> ConfigReloadResult:
        """Apply the stored configuration to this running server.

        Two phases, and only the second touches anything running.
        Preparation re-reads the stored half, composes and validates the
        world this server may serve, synthesizes its filled pauses and
        builds every MCP manager that world needs; any failure there (a
        stored snapshot that will not compose, an overlay that no longer
        does, an unset `$VAR`, a credential that will not decrypt, an
        entry `server.local_only` forbids) refuses with the generation
        and the managers exactly as they were. Application then swaps the
        generation and stops, starts and installs the MCP world, so what
        a new activation binds moves at one instant rather than across
        one.

        A synthesis that fails is the one failure in there that is not a
        refusal: the world applies with no clip for that agent and the
        answer names it, because a filled pause is a mask and a reload
        that refused over one would hold back everything else it was
        asked to apply.

        Being unreachable is not a preparation failure, which is the
        boot's rule carried over: a candidate that connects to nothing
        applies as a down manager with its reason on the status surface,
        revived when a session that would use it opens.

        One at a time. A second apply while one is running is refused
        rather than queued, because it would carry a configuration read
        later than the first one's into a world the first one is halfway
        through changing.

        The second phase finishes whatever happens to the caller. A
        client that disconnects cancels the handler awaiting this, and a
        cancellation landing between the generation swap and the MCP
        install would leave a server serving one half of each world with
        the exclusion released as though the apply were done. So the
        apply runs in a task of its own behind a shield: cancelling the
        request cancels the waiting, and the world still arrives in one
        piece.

        The preparation is behind a shield of its own, and for a
        different reason. Nothing it does can leave a half-changed
        world, but its re-read runs in a worker thread, and a thread
        cannot be cancelled: a client that disconnects during it leaves
        it holding the database's write lock for as long as it takes.
        Releasing the exclusion there would let the next apply start a
        read against a lock the last one still holds, and answer a
        caller who did nothing wrong that the database is busy. So both
        halves are owned tasks, and the exclusion is held until
        whichever of them is still running has finished.
        """
        if self._running:
            mcp.refused(ReloadInProgressError(RELOAD_IN_PROGRESS))
            raise ReloadInProgressError(RELOAD_IN_PROGRESS)
        self._running = True
        # When the request was accepted, which is what the applied
        # event's duration is measured from: a reload spends its time in
        # whichever half is slow, and the number an operator reads is
        # what they waited.
        began = time.monotonic()
        preparing: asyncio.Task[_Candidate] | None = None
        applying: asyncio.Task[ConfigReloadResult] | None = None
        try:
            preparing = asyncio.create_task(self._prepare(), name="config-reload-read")
            self._preparing = preparing
            candidate = await asyncio.shield(preparing)
            applying = asyncio.create_task(
                self._apply(candidate, began), name="config-reload"
            )
            self._applying = applying
            return await asyncio.shield(applying)
        finally:
            # The apply exists only once the preparation returned, so
            # the later of the two is the one still capable of running,
            # and it is the one the exclusion waits on.
            self._hold_until(applying if applying is not None else preparing)

    async def _prepare(self) -> _Candidate:
        """The first phase, whole: the re-read, the world it composes,
        and every manager that world needs, with nothing running
        touched.

        Gathered into one call so that the refusal has one place to be
        said out loud. Every half refuses the same way as far as a
        caller is concerned (nothing changed, and here is why), and an
        operator counting refused reloads does not care which half of a
        preparation the refusal came out of.

        A cancellation is not a refusal and is not reported as one: the
        caller went away before the world was even a candidate, and
        nothing happened that anybody has to be told about.

        The two failure branches differ in one thing only: whether the
        exception is already this application's own words. A
        `ConfigError` is, by construction, since every raise below
        composes its message outside its own handler and names
        configuration locations rather than values. Anything else came
        from the `read` callable, whose failures nothing bounds, so it
        is classified here and answered with a fixed sentence built
        after the handler has closed: the token is the diagnosis, and
        what a database driver had to say about a connection string is
        not part of the reload's answer.
        """
        problem: str | None = None
        try:
            stored = await self._stored()
            previous = self._generations.current()
            overlaid = _composed(previous.config, stored.config)
            # Synthesized here, in the phase that can only refuse, and
            # deliberately not able to refuse: an agent whose voice would
            # not speak applies with no clip and runs with the mask off,
            # because a filler is a latency mask and a posture where a
            # text-to-speech hiccup blocked a prompt fix would invert
            # what matters. Every clip whose phrases and whose voice are
            # what they were is the object it already was.
            fillers = await build_agent_fillers(
                overlaid, self._agent_providers, previous
            )
            # The MCP half is built from the stored world and not from
            # the overlay, because the MCP half is not overlaid: entries
            # and grants are what a reload has always applied whole, and
            # the managers this builds are the ones the install puts in
            # place.
            return _Candidate(
                generation=Generation(
                    overlaid,
                    stored.secrets.composed(previous.secrets, LIVE_SECRETS),
                    fillers.clips,
                ),
                prompts=_reassembled(previous.config, overlaid),
                fillers=fillers,
                mcp=await mcp.prepare(stored.config, stored.secrets),
            )
        except asyncio.CancelledError:
            raise
        except ConfigError as exc:
            # Every refusal this phase can end at passes here, whichever
            # half raised it, which is what makes one reload one event.
            # The three retryable and unreadable types are `ConfigError`s
            # too, and the token they are classified into is their type.
            mcp.refused(exc)
            raise
        except Exception as exc:
            mcp.refused(exc)
            problem = RELOAD_UNREADABLE
        raise StorageError(problem)

    async def _stored(self) -> Loaded:
        """The stored configuration, read off this loop, refusing in the
        words the rest of the preparation refuses in.

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
            return await asyncio.to_thread(self._read)
        except (DatabaseBusyError, StorageError):
            raise
        except ConfigError as exc:
            problem = f"{mcp.RELOAD_REFUSED} {exc}"
        raise ConfigError(problem)

    async def _apply(self, candidate: _Candidate, began: float) -> ConfigReloadResult:
        """The second phase: the world put in place, and the answer.

        Both swaps happen inside one instability window, so a reader
        that samples the mark either side of an await sees one world or
        refuses; between them a session activating gets the new prompts
        with the old tool world for at most one utterance, which is the
        per-half convergence the reload has always had rather than
        something to hold a lock across an await for.

        The generation goes first because it is the awaitless one: the
        MCP install stops and starts managers around its own swap, and
        putting the assignment after that work would widen the window
        the other way round for nothing.
        """
        with self._generations.applying() as install:
            install(candidate.generation)
            applied = await mcp.apply(self._servers, candidate.mcp, began)
        return ConfigReloadResult(
            mcp=applied,
            prompts=PromptsReload(changed=list(candidate.prompts)),
            fillers=FillersReload(
                resynthesized=list(candidate.fillers.resynthesized),
                reused=list(candidate.fillers.reused),
                disabled=list(candidate.fillers.disabled),
            ),
            # Null rather than empty until the milestones that fill
            # them: see `ConfigReloadResult`.
            providers=None,
            agents=None,
        )

    def _hold_until(self, running: "asyncio.Task[Any] | None") -> None:
        """Keep the exclusion until this half of the apply has really
        finished, whatever happened to the caller.

        A second apply starting against a world the first is still
        changing, or against a database lock the first is still holding,
        is exactly what the exclusion is for, and a cancelled caller
        stops neither of those from being true.
        """
        if running is None or running.done():
            self._release(running)
        else:
            running.add_done_callback(self._release)

    def _release(self, finished: "asyncio.Task[Any] | None") -> None:
        """The apply is over, however it ended. Also where a half whose
        caller went away has its outcome consumed, so it does not end as
        an unretrieved exception at shutdown: that is true of a
        preparation a client abandoned mid-read as much as of an
        apply."""
        self._running = False
        self._applying = None
        self._preparing = None
        if finished is not None and finished.done():
            with contextlib.suppress(Exception, asyncio.CancelledError):
                finished.exception()


def _composed(previous: Config, stored: Config) -> Config:
    """The world this server may serve once the stored half has been
    read: the previous configuration with the live slices replaced, and
    nothing else.

    The overlay, and its whole rule for this milestone: the shared
    prompt fragments as the store holds them, and each retained agent's
    own `prompt`, `prompt_includes` and `filler`. The agent set does not
    move, so an agent the store has added is not served and an agent it
    has deleted is still served; `agent_defaults` does not move either,
    so an edit to the fragments or the filled pauses every agent
    inherits stays pending until the restart that reads it. That is not
    caution: the effective-value helpers inherit through
    `agent_defaults`, so a candidate that took it would apply a
    restart-bound change through the back door, and one that took the
    agent set would let an activation index an entry that is not there.

    Validated whole, by the same two checks every composition passes, so
    a combination of live and restart-bound slices that does not add up
    is refused rather than served. Deleting a fragment while a
    restart-bound `prompt_includes` still names it is the case that
    reaches this, and the sentence it refuses with is the one the boot
    would print.
    """
    agents = {
        name: agent
        if (fresh := stored.agents.get(name)) is None
        else agent.model_copy(update={name: getattr(fresh, name) for name in OVERLAID_AGENT_FIELDS})
        for name, agent in previous.agents.items()
    }
    # `model_copy` rather than a re-validation of a dumped snapshot:
    # every value here came out of a model that has already been
    # validated field by field, so what is left to check is the whole,
    # which `_validated` does with the very functions the model's own
    # validator calls.
    overlaid = previous.model_copy(
        update={"prompt_fragments": dict(stored.prompt_fragments), "agents": agents}
    )
    _validated(overlaid)
    return overlaid


def _validated(candidate: Config) -> None:
    """The whole-snapshot checks, run again on a world that was composed
    rather than loaded.

    The same two `Config`'s own validator runs, in the same order, so an
    overlay is held to exactly what a boot is held to. Recorded and
    raised outside the handler is not needed here because nothing is
    caught: the problems are the checks' own sentences, which name
    configuration locations.
    """
    problems = check_completeness(candidate) + check_references(candidate)
    if problems:
        raise ConfigError("\n".join([mcp.RELOAD_REFUSED, *problems]))


def _reassembled(previous: Config, applied: Config) -> tuple[str, ...]:
    """The agents whose know-how would be assembled differently now.

    The two inputs this milestone makes live, read through the same
    effective-value helpers an activation reads them through, so an
    agent is reported here exactly when its next activation would
    produce different text. An agent only one side holds is not
    compared: the agent set does not move at a reload, so a difference
    there is not something this apply did.
    """
    return tuple(
        sorted(
            name
            for name in applied.agents
            if name in previous.agents
            and (
                previous.prompt_for_agent(name) != applied.prompt_for_agent(name)
                or previous.fragments_for_agent(name) != applied.fragments_for_agent(name)
            )
        )
    )


__all__ = ["LIVE_SECRETS", "RELOAD_IN_PROGRESS", "RELOAD_UNREADABLE", "ConfigReload"]
