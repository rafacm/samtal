"""Applying the stored configuration to a running server.

One verb, and it is the configuration's rather than any one kind's
(#191). The MCP reload proved the shape: a prepare phase that can only
refuse, then a swap of what new work binds to. What is here is that
shape generalized, so that a caller asks for the stored configuration to
be applied and never for one half of it, and the halves that converge at
different moments say so in the answer rather than in the caller's head.

Two phases and one exclusion.

Preparation re-reads the stored domain half, composes the world this
server may actually serve, validates it whole, builds the engines that
world speaks through, synthesizes the filled pauses it needs and builds
every MCP manager it names, with nothing running touched: a failure
anywhere in it is a refusal that changed nothing. Application then puts
the world in place, generation first and MCP install after it, inside
one instability window, so a reader either sees the world before or the
world after and never a mixture.

Building the engines is where a preparation spends its time and its
memory, and it is deliberately paid before anything can refuse: the
egress rule can only be checked on a built provider, so a refused apply
has loaded a model to find out. That is the price of the promise that a
refusal touched nothing running, and it is paid only for what actually
moved: an entry whose definition and stored credential are what they
were is carried into the new world as the object it already was, so an
edit to a prompt reloads nothing at all.

The one thing preparation does that cannot refuse is the synthesis. A
filled pause is a latency mask, so an agent whose voice will not speak
applies with no clip and runs with the mask off, exactly as a boot
leaves it; a posture where a text-to-speech hiccup blocked a prompt fix
would invert which of the two matters.

The world a generation serves is the stored domain half WHOLE, which is
the part worth reading twice, because it was not always so. Through the
milestones that built this, the candidate was an overlay: the previous
generation's configuration with only the slices this server could really
apply replaced from the store, because the agent set, `agent_defaults`
and each agent's choice of provider entry were start-bound and the
effective-value helpers inherit through exactly those. Nothing in the
domain half is start-bound now, so the overlay is gone and the candidate
is what the store describes, composed onto this process's own server
section by the read and validated whole by that same read. What is left
of the old boundary is the file half, which a reload never re-reads: the
port, the directories, the limits, and everything else `server` holds.

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
import logging
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from typing import Any

from vinga_server.config.diff import Loaded, unchanged_providers
from vinga_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    ProviderRefusedError,
    ReloadInProgressError,
    StorageError,
)
from vinga_server.config.models import Config
from vinga_server.config.responses import (
    AgentsReload,
    ConfigReloadResult,
    FillersReload,
    PromptsReload,
    ProvidersReload,
)
from vinga_server.config.secrets import EntityKind
from vinga_server.filler import Fillers, build_agent_fillers
from vinga_server.generation import Generation, Generations
from vinga_server.providers import Built, Provider, ProviderError, build_world
from vinga_server.tools.mcp import McpServers
from vinga_server.tools.mcp import reload as mcp

logger = logging.getLogger(__name__)

# Which entity kinds' stored credentials this server applies rather than
# carries over. An MCP server's are read as its connection is made, and
# a reload makes it again; a provider's are read as the provider is
# built, which an apply now does too, so a rotation on either is applied
# rather than left pending. Declared as data beside the apply it
# governs, and widened one member at a time; with both kinds in it, the
# derivation below composes nothing and the whole stored store is the
# candidate's, which is what the last member arriving means.
LIVE_SECRETS: frozenset[EntityKind] = frozenset({"mcp_server", "provider"})

# What a reload says when it could not build the engines the stored
# world names.
#
# Fixed, and interpolating nothing, for the reason the composition
# root's refusals are: what a provider build refuses on is stored state,
# and the sentences the provider layer composes name the entry, the type
# and the option key it choked on. An operator who pasted a credential
# into an option name would find it in this answer, so the diagnosis is
# where a diagnosis belongs, which is a server started from this store,
# and the class of the failure goes to this server's log.
_PROVIDERS_REFUSED = (
    f"{mcp.RELOAD_REFUSED} the engines the stored configuration names could not all be "
    "built: a provider type, one of its options, its stored credential or the egress "
    "rule refused. Which one is deliberately not said here, because a sentence about a "
    "stored value is the one thing a reload's answer never carries. A server started "
    "from this store refuses on the same state and names the location it refused on, "
    "and the failure's kind is recorded in this server's log. Nothing was changed: the "
    "engines this server is running are the ones it was running before this request."
)

_RELOAD_IN_PROGRESS = (
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
_RELOAD_UNREADABLE = (
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
    providers: Built
    retired: tuple[str, ...]
    agents: AgentsReload
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
        held: Callable[[], Collection[Generation]] = tuple,
    ) -> None:
        self._generations = generations
        self._servers = servers
        # Who is still holding a world, asked at the one moment the
        # answer decides something: after the swap, when the world this
        # apply replaced may or may not be lettable-go-of. A callable
        # rather than the session registry itself, because what an apply
        # needs to know about the conversations in flight is exactly
        # this one sentence, and the default is the true answer for an
        # application with no sessions around it.
        self._held = held
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
        # And the letting-go of a preparation nobody installed, held for
        # the same reason both of those are: it outlives the request
        # that started it, and it is what the exclusion waits on when
        # there was never an apply.
        self._discarding: asyncio.Task[None] | None = None

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
        stored snapshot that will not compose, an unset `$VAR`, a
        credential that will not decrypt, an entry `server.local_only`
        forbids) refuses with the generation
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
            mcp.refused(ReloadInProgressError(_RELOAD_IN_PROGRESS))
            raise ReloadInProgressError(_RELOAD_IN_PROGRESS)
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
            # and it is the one the exclusion waits on. Leaving without
            # one is leaving with a preparation that may still be
            # building a world nothing will install, which is the one
            # exit that has something to let go of.
            if applying is not None:
                self._hold_until(applying)
            else:
                self._discard(preparing)

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
            # The stored half whole, which is the last thing the overlay
            # had left to say (#191). Nothing in the domain half is
            # start-bound any more, so the world to serve is the world
            # the store describes, composed onto this process's own
            # server section by the read itself and validated whole by
            # the same checks a boot passes.
            composed = Generation(
                stored.config,
                stored.secrets.composed(previous.secrets, LIVE_SECRETS),
            )
            # The engines first, because everything after them needs
            # them: a clip is spoken by this world's voice. Every entry
            # whose definition and stored credential are what they were
            # is the object it already was, which is what keeps an edit
            # to a prompt from reloading a local model.
            providers = await self._built(previous, composed)
            try:
                # Both kinds of cached speech, synthesized here, in the
                # phase that can only refuse, and deliberately not able
                # to refuse: an agent whose voice would not speak applies
                # with the mask off and its failure phrase shown rather
                # than spoken, because a posture where a text-to-speech
                # hiccup blocked a prompt fix would invert what matters.
                # Every clip whose effective section and whose voice are
                # what they were is the object it already was, and the
                # voice is this candidate's now: an entry the build above
                # made again is a different one, so its agents are spoken
                # again in it. The two kinds ask that question of their
                # own sections, so neither stales the other.
                fillers = await build_agent_fillers(
                    composed.config, providers.world.agents, previous
                )
                # The MCP half, built from the same world: what a
                # generation serves and what its managers were composed
                # from are one snapshot now that nothing is held back.
                candidate = await mcp.prepare(stored.config, stored.secrets)
            except BaseException:
                # The engines above are this preparation's until
                # something installs them, and a refusal from either
                # half below is an exit that is not an install.
                await providers.close()
                raise
            return _Candidate(
                generation=replace(
                    composed,
                    fillers=fillers.clips,
                    providers=providers.world,
                    fallbacks=fillers.fallbacks,
                ),
                prompts=_reassembled(previous.config, composed.config),
                fillers=fillers,
                providers=providers,
                retired=_retired(previous, providers),
                agents=_agents(previous.config, composed.config),
                mcp=candidate,
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
            problem = _RELOAD_UNREADABLE
        raise StorageError(problem)

    async def _built(self, previous: Generation, candidate: Generation) -> Built:
        """The engines the candidate world would speak through, in the
        vocabulary this endpoint refuses in.

        Two jobs, and the second is why it is a method rather than a
        call. `ProviderError` is the provider layer's contract and is
        not a `ConfigError`, so nothing above would know what status it
        meant; here it becomes the one typed refusal an apply answers a
        failed build with, carrying a sentence that names nothing
        stored. The class goes to the log, which is where a diagnosis
        that cannot be safely said belongs, and the sentence is composed
        after the handler has closed so that neither the original nor
        anything it was holding travels with it.
        """
        problem: str | None = None
        try:
            return await build_world(
                candidate.config, candidate.secrets, _carried(previous, candidate)
            )
        except ProviderError as exc:
            # The class and never the message: what the provider layer
            # composes names the entry, the type and the option it
            # refused on, all of them stored.
            logger.warning(
                "a reload could not build the stored world's providers (%s)",
                type(exc).__name__,
            )
            problem = _PROVIDERS_REFUSED
        raise ProviderRefusedError(problem)

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

        The teardown comes after the window rather than inside it, and
        after everything that decides the answer. A world that has just
        stopped being current lets go of the engines nothing else holds,
        which is usually the same instant, and a conversation still
        speaking through one of them holds it until it ends. Whatever
        that teardown does, this apply has already installed, the mark
        has already settled and the answer below is already decided: a
        client whose connection pool will not shut cannot turn an
        applied world into a refusal.
        """
        with self._generations.applying() as install:
            install(candidate.generation)
            # The transfer, said in the window that performs it: what
            # the preparation built is the installed generation's now,
            # so the discard path below cannot close a world this server
            # is serving.
            candidate.providers.installed()
            applied = await mcp.apply(self._servers, candidate.mcp, began)
        await self._generations.dispose(self._held())
        return ConfigReloadResult(
            mcp=applied,
            prompts=PromptsReload(changed=list(candidate.prompts)),
            fillers=FillersReload(
                resynthesized=list(candidate.fillers.resynthesized),
                reused=list(candidate.fillers.reused),
                disabled=list(candidate.fillers.disabled),
                fallback_resynthesized=list(candidate.fillers.fallback_resynthesized),
                fallback_reused=list(candidate.fillers.fallback_reused),
                fallback_degraded=list(candidate.fillers.fallback_degraded),
            ),
            providers=ProvidersReload(
                built=list(candidate.providers.built),
                reused=list(candidate.providers.reused),
                retired=list(candidate.retired),
            ),
            agents=candidate.agents,
        )

    def _discard(self, preparing: "asyncio.Task[_Candidate] | None") -> None:
        """Give the exclusion back once a preparation nobody is going to
        install has finished, and let go of anything it built.

        The ordinary ending here is a refusal, which built nothing and
        gives the exclusion back at once: a caller told that its apply
        was refused may make the next one immediately. What needs
        waiting for is the other two: a preparation still running behind
        a caller that went away, and one that finished with a world in
        its hands.
        """
        if preparing is None:
            self._release(None)
        elif preparing.done() and (preparing.cancelled() or preparing.exception()):
            self._release(preparing)
        else:
            self._discarding = asyncio.create_task(
                self._discarded(preparing), name="config-reload-discard"
            )

    async def _discarded(self, preparing: "asyncio.Task[_Candidate]") -> None:
        """See out a preparation nobody is going to install, and let go
        of what it built.

        The exit the ownership rule exists for. A preparation is
        shielded from its caller, so a client that disconnects mid-read
        leaves it running, and what it eventually answers with is a
        world with engines in it that no generation will ever hold. The
        exclusion is held until it has finished either way, which it
        already was; what is new is that the engines go with it.

        Every ending is the same ending here. A preparation that refused
        has nothing to close, one that was cancelled closed its own as
        it went, and one that succeeded is closed here.
        """
        candidate: _Candidate | None = None
        with contextlib.suppress(Exception, asyncio.CancelledError):
            candidate = await preparing
        if candidate is not None:
            await candidate.providers.close()
        self._release(preparing)

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
        self._discarding = None
        if finished is not None and finished.done():
            with contextlib.suppress(Exception, asyncio.CancelledError):
                finished.exception()


def _agents(previous: Config, applied: Config) -> AgentsReload:
    """Which agents this server can serve now that it could not before,
    which it can no longer be asked for, and whether the layer they all
    inherit from moved.

    The last kind to become a reload's business, and the one that makes
    the other four honest: an added agent is servable from the swap, so
    a device bound to it reaches it at its next check-in, and a removed
    one is unreachable to anything that starts after it while every
    conversation already talking as it finishes on the world it was
    built from.

    `agent_defaults` is a boolean because there is one of it and nothing
    to name. Compared whole, grants included: everything on that layer
    is applied now, and what it changes reaches whichever agents inherit
    the field that moved.
    """
    return AgentsReload(
        added=sorted(set(applied.agents) - set(previous.agents)),
        removed=sorted(set(previous.agents) - set(applied.agents)),
        defaults_changed=previous.agent_defaults != applied.agent_defaults,
    )


def _carried(previous: Generation, candidate: Loaded) -> dict[str, Provider]:
    """The engines the candidate world may go on using: every entry the
    two worlds define identically, credentials included.

    The comparison is the one the stored-versus-running read answers
    with, read from the module that owns it rather than written again
    here, because "is this the same provider" is one question and two
    answers to it would eventually disagree about a rotated credential.

    What comes back is offered to the builder, which uses it rather than
    constructing. Everything else about the candidate world is built,
    which is what makes a rewritten entry a new object and an untouched
    one the object a conversation may still be speaking through.
    """
    unchanged = unchanged_providers(previous, candidate)
    return {
        identity: provider
        for identity, provider in previous.providers.instances.items()
        if identity in unchanged
    }


def _retired(previous: Generation, providers: Built) -> tuple[str, ...]:
    """The entries the world before this one was running and this one
    does not name at all.

    Not the same as what is closed, and deliberately reported rather
    than the closing: an entry named here is one no world after this
    apply uses, while when its engine is actually released depends on
    the conversations still holding it. A rewritten entry is not here,
    because the name is still served; it is under `built`.
    """
    return tuple(sorted(set(previous.providers.instances) - set(providers.world.instances)))


def _reassembled(previous: Config, applied: Config) -> tuple[str, ...]:
    """The agents whose know-how would be assembled differently now.

    The prompt inputs, read through the same effective-value helpers an
    activation reads them through, so an agent is reported here exactly
    when its next activation would produce different text. An agent only
    one side holds is not compared: an agent this apply added has no
    previous text to differ from and one it removed will not assemble
    again, and both ride the `agents` section instead.
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


__all__ = [
    "LIVE_SECRETS",
    "ConfigReload",
]
