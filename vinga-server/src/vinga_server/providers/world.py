"""The engines one world runs on: built, owned, and let go of.

Building a provider used to be a thing a server did once. The object
that came back lived as long as the process, so nobody owned it after
the boot and nothing was ever released; a failure part way through the
boot simply took the process with it. Applying stored configuration
without a restart ends both of those (#191): an entry an apply rewrote
is built again, the object the old world spoke through has to be told
its world is over, and a build that refuses happens while a server is
serving conversations and must leave them exactly as they were.

So ownership is total here, and it begins the instant an allocation
succeeds rather than when a finished world is handed over. Three rules
carry it:

- Options are read and finished before anything is constructed, in the
  provider modules themselves, so a trailing unknown option refuses
  without a model having been loaded.
- One provider is constructed at a time, off the loop, and the object it
  returns transfers into this module before anything can refuse it. The
  egress check therefore runs here, on the loop, inside the owner that
  is already holding the object, so a refusal closes what it just built
  instead of dropping it.
- Every exit that is not an install closes what this build constructed,
  exactly once, and nothing is left to the garbage collector. A later
  entry's failure, a preparation whose caller went away, a boot that
  refuses: all one path.

What is deliberately not owned here is a carried-over provider. An entry
whose model and stored credentials are unchanged is the object it
already was, and the world that built it goes on holding it: reuse
transfers a share of ownership rather than making a second one, which is
why a failed build closes what it constructed and never what it kept.

Disposal is the other half and is written to be unrefusable. It runs
after the world has already moved, so a `close` that raises cannot fail
anything: it is bounded, classified by exception class with the prose
dropped, and the caller carries on. The only thing it waits for is work:
a provider's own `close` waits out the worker threads still inside it,
which is what keeps an engine reachable until the transcription that was
already running has finished with it.
"""

import asyncio
import contextlib
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import cast

from vinga_server.build_info import in_container
from vinga_server.config.models import PROVIDER_STAGES, Config, ProviderConfig
from vinga_server.config.secrets import SecretStore, provider_identity
from vinga_server.egress import EgressRefusal, check_provider
from vinga_server.events import ServerEvents
from vinga_server.events.catalog import ProviderReachesLoopback
from vinga_server.events.values import Identifier, LoopbackHost
from vinga_server.providers.base import (
    AsrProvider,
    LlmProvider,
    Provider,
    ProviderError,
    ProviderIdentity,
    TtsProvider,
    VadProvider,
)
from vinga_server.providers.registry import AgentProviders, construct_provider

logger = logging.getLogger(__name__)

events = ServerEvents(__name__)

# How long one teardown is given, whatever it is letting go of. A bound
# rather than a wait, because disposal runs after the swap: a client
# whose pool will not shut, or an engine whose worker thread never
# returns, is a resource this process keeps, and it is not a reason for
# an operator's request to hang.
#
# One bound for the operation and not one per provider. What a caller
# is waiting through is a teardown, and a per-provider bound would make
# what they wait depend on how many entries a world happened to hold:
# ten stuck clients would be a hundred seconds of an apply that had
# already applied. Closes are independent of one another, so they run
# together and the deadline covers the lot.
DISPOSAL_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class ProviderWorld:
    """The engines one generation serves with: what each agent talks
    through, and the unique objects behind them.

    Two views of one set, and both are needed. `agents` is what a
    conversation is built from, per agent and stage; `instances` is the
    same objects keyed by the entry each was built from
    (`<stage>.<name>`), which is the view a lifecycle needs: one entry
    shared by four agents is one object to build, one to report, and one
    to close.

    Empty is the honest answer for a world with no agents, which is what
    a test that never opens a conversation composes.
    """

    agents: Mapping[str, AgentProviders] = field(default_factory=dict)
    instances: Mapping[str, Provider] = field(default_factory=dict)


@dataclass
class Built:
    """One preparation's answer: the world it composed, and how each
    entry got into it.

    The two name lists are the closed outcomes of the one decision this
    module makes per entry, recorded where it is made rather than
    reconstructed by a caller comparing two mappings. What is not here
    is what was retired, which is not this build's decision: it is what
    the world before this one held and this one does not, and only
    something holding both can say it.
    """

    world: ProviderWorld
    built: tuple[str, ...] = ()
    reused: tuple[str, ...] = ()
    # Whether something else has taken this world on. Not an argument:
    # a build always comes back owning what it made, and the transfer
    # is an act the owner performs rather than a state a caller can
    # declare at construction.
    _installed: bool = field(default=False, init=False, repr=False)

    def installed(self) -> ProviderWorld:
        """Hand this world over: what it holds is somebody else's from
        now on, and this owner will not close it.

        The transfer the ownership rule turns on. Between a build
        returning and a generation being installed there is a stretch
        that can still fail, and until this is called that stretch is
        covered: closing this owner closes what the build made. After
        it, the generation is what closes them, at the end of the apply
        that replaced it or at the end of the process.

        Answers the world it is handing over, so that the transfer and
        the read are one statement at the call site rather than two that
        could come apart.
        """
        self._installed = True
        return self.world

    async def close(self) -> None:
        """Let go of what this preparation constructed, and of nothing
        it carried over.

        What a caller reaches for when a built world is never installed:
        a preparation whose caller had gone by the time it finished, an
        apply refused after it, or a boot that failed between this build
        and the holder it was going to live in. The carried-over objects
        are the running world's and stay exactly as they are, which is
        what makes a refused apply a thing that touched nothing.

        A no-op once the world has been handed over, so that registering
        this on an exit stack in front of the transfer is safe: what a
        generation owns is closed by the generation, once.
        """
        if self._installed:
            return
        await disposed(self.world.instances[identity] for identity in self.built)


async def build_entry(
    stage: str,
    name: str,
    config: ProviderConfig,
    local_only: bool = False,
    secrets: SecretStore | None = None,
) -> Provider:
    """Build the provider behind `providers.<stage>.<name>`, owned from
    the moment it exists.

    Raises `ProviderError` for an unknown type, a bad option, a missing
    extra, an egress-marked provider under `local_only`, a class
    carrying no egress marking of its own, or anything the provider
    itself raises while constructing. Every one of them names the entry,
    and none of them leaves an object behind: a refusal after the
    construction closes what it is refusing.

    The construction runs off the loop because that is where a boot
    spends its time (an ASR or VAD provider loads a model, which is
    seconds to minutes of blocking work) and because the loop it would
    otherwise run on is the one every live conversation is on.

    A cancellation landing while that thread is inside a third-party
    constructor is owned too, and it is the case the shape below exists
    for. A thread cannot be cancelled: the constructor runs to its end
    whatever happened to whoever was awaiting it, and the object it
    finally returns would have nobody to hand it to. So the construction
    is a future this function keeps rather than an await it abandons, and
    a cancelled caller waits out the thread it started, closes what came
    back and then goes on being cancelled.
    """
    label = f"providers.{stage}.{name}"
    constructing = asyncio.ensure_future(
        asyncio.to_thread(construct_provider, stage, name, config, secrets)
    )
    try:
        provider = await asyncio.shield(constructing)
    except asyncio.CancelledError:
        await _abandoned(constructing)
        raise
    # Owned from this line. Everything below can refuse, and everything
    # below closes what it refuses.
    #
    # The egress rule itself lives in one module that this builder and
    # the MCP build path both call (#30, #136); what stays here is the
    # exception type, which is this surface's contract, wrapped around
    # the module's own sentence. Recorded and raised outside the
    # handler, this codebase's rule, and load bearing here for a second
    # reason: the close is an await, and an await inside an `except` arm
    # would leave the refusal carrying whatever the disposal did.
    refusal: str | None = None
    try:
        check_provider(label, config, provider, local_only)
    except EgressRefusal as exc:
        refusal = str(exc)
    if refusal is not None:
        await disposed([provider])
        raise ProviderError(refusal)
    # Stamped here rather than in each factory: this is the one place
    # that knows the stage, the entry name and the type at once, and a
    # provider that failed to describe itself in an event would be a
    # provider the operator cannot map back to their configuration.
    provider.identity = ProviderIdentity(
        stage=stage,
        name=name,
        type=config.type,
        host=provider.host,
        model=provider.model,
    )
    _loopback_inside_a_container(stage, name, config, provider)
    return provider


def _loopback_inside_a_container(
    stage: str, name: str, config: ProviderConfig, provider: Provider
) -> None:
    """Say so when a container's entry points at the container itself.

    The failure this exists for looks like nothing at all (#340). A
    `base_url` naming localhost is what an operator runs on their own
    machine, and copied into a container it still boots clean, still
    applies clean, and still hears the utterance; the first sign of it
    is a call that fails at the first round, on a device that shows
    nothing. Whether this process is inside a container is a thing the
    image knows and says (`build_info.in_container`), and whether the
    endpoint is this machine is three spellings, so the check is two
    reads and belongs where the answer is: this is the one place that
    holds the stage, the entry, the type and the host at once.

    A warning and never a refusal, because the same configuration is
    right where the endpoint shares this container or its network
    namespace, and only the deployment knows which it is.

    Every type that resolves a host is covered rather than the LLM one
    the issue met, and it costs nothing: `provider.host` is the parsed
    hostname each openai-shaped type already publishes for its identity
    and its failure events, so the openai TTS and ASR types are in by
    construction. A type that reaches nothing leaves it None and cannot
    match, and a type with a fixed vendor host never spells one of the
    three.
    """
    if not in_container():
        return
    try:
        host = LoopbackHost(provider.host)
    except ValueError:
        return
    events.emit(
        lambda: ProviderReachesLoopback(
            stage=Identifier(stage),
            provider=Identifier(name),
            type=Identifier(config.type),
            host=host,
        )
    )


async def _abandoned(constructing: "asyncio.Future[Provider]") -> None:
    """See out a construction nobody is going to own, and close what it
    made.

    The awaiting side has been cancelled and the worker thread has not,
    so this waits for the thread rather than for the caller, absorbing
    the cancellation as many times as it arrives. What comes back is
    closed; a construction that refused instead has nothing to close and
    its exception is the caller's own business, which is already on its
    way out.

    Bounded by the constructor rather than by a deadline, deliberately:
    a model that takes two minutes to load takes two minutes to load,
    and abandoning the wait would put the object back where this
    function found it, which is nowhere.
    """
    while not constructing.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(constructing)
    if constructing.cancelled() or constructing.exception() is not None:
        return
    await disposed([constructing.result()])


async def build_world(
    config: Config,
    secrets: SecretStore | None = None,
    carried: Mapping[str, Provider] | None = None,
) -> Built:
    """Every provider the configured agents reference, built or carried
    over, with the whole of it owned until a caller installs it.

    Agents are read through their effective view, so a stage comes from
    the agent or from `agent_defaults`, and one entry named by several
    agents is one object: the dedup is by entry identity within this
    build, exactly as it has always been.

    `carried` is what a previous world offers this one, keyed by entry
    identity. Which of its entries may be carried is not decided here,
    because "the same provider" is a question about two configurations
    and their stored credentials rather than about an object: the caller
    that holds both worlds decides it and hands over what survived. What
    this promises about them is only that they are used rather than
    built, and never closed by a failure of this build.

    The one path for a boot and for an apply alike, which is what makes
    the two agree about ownership rather than about a comment.
    """
    kept = dict(carried or {})
    instances: dict[str, Provider] = {}
    built: list[str] = []
    reused: list[str] = []
    agents: dict[str, AgentProviders] = {}
    try:
        for agent in config.agents:
            engines = {
                stage: await _stage_engine(
                    config, secrets, kept, instances, built, reused, agent, stage
                )
                for stage in PROVIDER_STAGES
            }
            agents[agent] = AgentProviders(
                llm=cast(LlmProvider, engines["llm"]),
                asr=cast(AsrProvider, engines["asr"]),
                tts=cast(TtsProvider, engines["tts"]),
                vad=cast(VadProvider, engines["vad"]),
            )
    except BaseException:
        # Every exit that is not a return: a later entry that would not
        # build, an egress refusal, a caller that went away. What this
        # build constructed goes, exactly once; what it carried over is
        # the running world's and stays.
        await disposed(instances[identity] for identity in built)
        raise
    return Built(
        world=ProviderWorld(agents=agents, instances=instances),
        # Sorted, because these are read by a person and by a client
        # comparing two answers, and neither should see an order that
        # depends on how the agents were walked.
        built=tuple(sorted(built)),
        reused=tuple(sorted(reused)),
    )


async def _stage_engine(
    config: Config,
    secrets: SecretStore | None,
    kept: Mapping[str, Provider],
    instances: dict[str, Provider],
    built: list[str],
    reused: list[str],
    agent: str,
    stage: str,
) -> Provider:
    """The object one agent's stage resolves to, built once per entry.

    The decision site for the reuse outcome, which is why the two lists
    are written here: an entry is carried over, or it is constructed,
    and there is no third answer for an entry a world needs.
    """
    name, _ = config.provider_for_agent(agent, stage)
    if name is None:
        raise ProviderError(
            f"agents.{agent}: no {stage} provider is named, and "
            f"agent_defaults.{stage} names none either; the conversation "
            f"pipeline needs all of: {', '.join(PROVIDER_STAGES)}"
        )
    identity = provider_identity(stage, name)
    if identity in instances:
        return instances[identity]
    carried_over = kept.get(identity)
    if carried_over is not None:
        instances[identity] = carried_over
        reused.append(identity)
        return carried_over
    entry = getattr(config.providers, stage)[name]
    provider = await build_entry(stage, name, entry, config.server.local_only, secrets)
    # Recorded as built the moment it exists, so that a failure of the
    # next entry closes this one.
    instances[identity] = provider
    built.append(identity)
    return provider


async def dispose(providers: Iterable[Provider]) -> None:
    """Close these providers, and answer whatever they did.

    Bounded and non-refusing, because of when it runs: after the world
    has already moved. A `close` that raises at that point cannot be
    turned into a refusal (there is nothing left to refuse), so it is
    classified by its exception class, the class alone is logged, and
    the next provider is closed. What a third-party client says while
    failing to shut is exactly the sort of sentence that quotes an
    endpoint or a credential, and this is not the place to find out.

    A close that hangs is bounded for the same reason: an operator's
    apply is not held open by a connection pool that will not settle.
    What is left in that case is a resource this process keeps until it
    ends, which is what the situation already was before any of this
    existed.

    Together rather than one after another, and under one deadline for
    all of them. Closing one provider has nothing to do with closing
    another, so a world of ten is a teardown of one bound rather than of
    ten, and what a caller waits through stops depending on how many
    entries the world it replaced happened to hold.
    """
    closing = {
        asyncio.ensure_future(provider.close()): provider for provider in providers
    }
    if not closing:
        return
    done, unfinished = await asyncio.wait(closing, timeout=DISPOSAL_TIMEOUT_S)
    for task in unfinished:
        # The bound expired. Cancelling is what makes the wait a bound
        # rather than a suggestion; the resource stays with this process
        # until it ends, which is where it already was.
        task.cancel()
    problems = [
        type(failure).__name__
        for task in done
        if not task.cancelled() and (failure := task.exception()) is not None
    ]
    for problem in problems:
        logger.warning(
            "a provider would not let go of what it holds (%s); its resources stay "
            "with this process until it ends",
            problem,
        )
    if unfinished:
        # Classified by what happened rather than by an exception class,
        # because nothing was raised: the close simply did not finish.
        logger.warning(
            "%d provider(s) did not let go of what they hold within %.0f s; their "
            "resources stay with this process until it ends",
            len(unfinished),
            DISPOSAL_TIMEOUT_S,
        )


async def disposed(providers: Iterable[Provider]) -> None:
    """`dispose`, run to its end even if whoever asked for it is
    cancelled.

    The shape a teardown on a failure path needs. The failure being
    handled is often a cancellation, and a disposal awaited plainly
    inside one would be abandoned at its first await, leaving exactly
    the resources the failure path exists to release. So the work is a
    task of its own, the cancellation is absorbed as many times as it
    arrives, and the caller's own exception carries on afterwards, which
    it can only do because the work below is bounded.
    """
    closing = asyncio.ensure_future(dispose(list(providers)))
    while not closing.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(closing)


__all__ = [
    "DISPOSAL_TIMEOUT_S",
    "Built",
    "ProviderWorld",
    "build_entry",
    "build_world",
    "dispose",
    "disposed",
]
